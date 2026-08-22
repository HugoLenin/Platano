# RUNBOOK — Emergency Language Bridge

Cómo levantar el sistema completo desde un clon limpio, generar el APK y correr
la demo. Los comandos de este archivo están probados en Windows 11 (Git Bash /
PowerShell); en macOS y Linux cambia solo el intérprete de Python (`python3.13`
en vez de `py -3.13`).

- Decisiones de diseño y gotchas conocidos → [`docs/DECISIONS.md`](docs/DECISIONS.md)
- Plantilla de WhatsApp y flujo de opt-in → [`docs/WHATSAPP_TEMPLATE.md`](docs/WHATSAPP_TEMPLATE.md)
- Estado detallado del traspaso → [`HANDOFF.md`](HANDOFF.md)

---

## 0. Camino más corto a algo que se vea

Sin credenciales de LiveKit / Deepgram / Anthropic / ElevenLabs **no hay
llamada**. Lo que sí corre sin ninguna key:

```bash
cd agent && PYTHONPATH=src py -3.13 -m pytest tests/ -q     # 31 passed, ~0.3 s
cd web   && npm install && npm run build                    # 9 rutas
cd android && ./gradlew assembleDebug                       # APK sideloadable
```

Con keys, el orden mínimo es: **web → agente → teléfono** (§4, §5, §6).

---

## 1. Prerrequisitos

| Herramienta | Versión | Para qué | Nota |
|---|---|---|---|
| Python | **3.13** | agente | `livekit-agents` exige ≥ 3.10 |
| Node.js | 20+ | web | |
| JDK | 17 | Android | `JAVA_HOME` apuntando ahí |
| Android SDK | Platform 37 + build-tools 36.0.0 | APK | vía Android Studio o `sdkmanager` |
| `adb` | cualquiera | instalar el APK | opcional si copias el APK a mano |

> [!WARNING]
> En la máquina donde se desarrolló esto, `python` es **3.9** y no sirve. Usa
> siempre **`py -3.13`**. Verifica con `py -3.13 --version`.

### Cuentas y API keys

| Servicio | Necesario para | Sin él |
|---|---|---|
| LiveKit Cloud | transporte de audio | no hay llamada |
| Deepgram | STT (`nova-3`) | no hay transcripción |
| Anthropic | traducción, extracción, reporte | no hay interpretación |
| ElevenLabs | TTS (`eleven_flash_v2_5`) | no hay voz de salida |
| Supabase | contactos, reportes, transcripciones | la llamada funciona; nada se guarda |
| Kapso o Meta WhatsApp | aviso a familiares | los envíos vuelven `skipped: true` |

Las últimas dos son **opcionales**: el sistema degrada en vez de romperse.

---

## 2. Variables de entorno

```bash
cp agent/.env.example agent/.env
cp web/.env.example   web/.env.local
```

### Los dos secretos compartidos

> [!IMPORTANT]
> `ELB_REPORT_SIGNING_SECRET` y `ELB_INTERNAL_TOKEN` **deben ser idénticos** en
> `agent/.env` y en `web/.env.local`. Si difieren: todo link firmado da 401 y el
> agente no puede disparar notificaciones. Es el error de configuración #1.

```bash
openssl rand -hex 32     # -> ELB_REPORT_SIGNING_SECRET (en LOS DOS archivos)
openssl rand -hex 24     # -> ELB_INTERNAL_TOKEN        (en LOS DOS archivos)
```

Sin `openssl` a mano:

```bash
py -3.13 -c "import secrets; print(secrets.token_hex(32))"
```

### Qué va en cada archivo

**`agent/.env`** — claves de los modelos y de LiveKit, más:

```bash
ELB_WEB_BASE_URL=http://localhost:3000    # base de los links de reporte
ELB_OPERATOR_LANG=es                      # idioma del despachador
```

**`web/.env.local`** — LiveKit (server + `NEXT_PUBLIC_LIVEKIT_URL`), Supabase,
WhatsApp, y los dos secretos compartidos.

`NEXT_PUBLIC_LIVEKIT_URL` debe tener **el mismo valor** que `LIVEKIT_URL`; una
va al navegador y la otra al servidor.

---

## 3. Base de datos (opcional pero recomendada)

Aplica el esquema en tu proyecto de Supabase:

```bash
supabase db push
# o: pega supabase/schema.sql en el SQL editor del dashboard
```

Trae **seed de demo**: un perfil (`Amara Okafor`, `user_id`
`11111111-1111-1111-1111-111111111111` — el mismo que trae el APK por defecto) y
dos contactos de confianza.

> Para demostrar WhatsApp de verdad, reemplaza los teléfonos del seed por
> números que controles y que hayan hecho opt-in
> (ver [`docs/WHATSAPP_TEMPLATE.md`](docs/WHATSAPP_TEMPLATE.md) §6).

---

## 4. Levantar la web

```bash
cd web
npm install
npm run dev          # http://localhost:3000
```

`predev` copia `shared/critical_terms.json` a `web/lib/` automáticamente.

Rutas:

| Ruta | Qué es |
|---|---|
| `/` | portada |
| `/operator` | consola del despachador (la que se usa en la demo) |
| `/r/<token>` | visor del reporte, renderizado según el `scope` del token |
| `/api/token` | mint de tokens de LiveKit (`role`: `caller` \| `operator`) |
| `/api/notify` | recibe el aviso del agente y despacha WhatsApp/push |
| `/api/contacts` | CRUD de contactos de confianza (lo usa la app) |
| `/api/report/[token]` | reporte en texto plano |
| `/api/whatsapp/webhook` | handshake de Meta + opt-in/opt-out entrante |

Verificación rápida:

```bash
curl -X POST http://localhost:3000/api/token \
  -H "Content-Type: application/json" \
  -d '{"role":"operator","room":"elb-demo","lang":"es"}'
```

Debe devolver un JWT. Si dice `LiveKit is not configured`, faltan
`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`.

---

## 5. Levantar el agente

```bash
cd agent
py -3.13 -m pip install -r requirements.txt
PYTHONPATH=src py -3.13 -m elb.main dev
```

`dev` es el modo del CLI de livekit-agents: se registra como worker
(`agent_name="elb-interpreter"`) y espera jobs. Cuando alguien entra a una sala,
el agente se une **dos veces** (una conexión por dirección de traducción, ver
[D5](docs/DECISIONS.md#d5--dos-agentsession-en-un-proceso-con-dos-conexiones-y-tracks-nombrados)).

Arranque correcto (verificado el 2026-08-22):

```
INFO livekit.agents  plugin registered {"plugin": "livekit.plugins.deepgram", ...}
INFO livekit.agents  registered worker {"agent_name": "elb-interpreter",
                     "url": "wss://...livekit.cloud", "region": "US East B"}
```

- `main.py` llama a `load_dotenv()` **antes** de importar `.config`, así que
  `agent/.env` se carga solo. No hace falta exportar nada a mano.
- El CLI imprime `dev mode is deprecated ... use 'lk agent dev'`. Es un aviso,
  no un error: `dev` sigue funcionando en livekit-agents 1.7.0.
- Si faltan claves, `Settings.missing_required()` lista cuáles.

---

## 6. Generar e instalar el APK

Probado: `assembleDebug` y `assembleRelease` dan **BUILD SUCCESSFUL**; el APK
debug pesa ~67 MB (4 ABIs, sin R8).

```bash
cd android

# Opcional pero necesario en teléfono físico: apuntar el APK a tu backend.
# Crear android/elb.properties (git-ignorado):
#   ELB_API_BASE=http://192.168.X.X:3000     <- IP de tu laptop en la LAN
#   ELB_WHATSAPP_NUMBER=+57...               <- número al que se manda el opt-in
#   ELB_DEFAULT_ROOM=elb-demo

./gradlew assembleDebug      # -> app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease    # -> app/build/outputs/apk/release/app-release.apk

adb install -r app/build/outputs/apk/debug/app-debug.apk
```

> [!WARNING]
> `ELB_API_BASE` por defecto es `http://10.0.2.2:3000`, que es **el localhost de
> la laptop visto desde el emulador**. En un teléfono físico hay que cambiarlo a
> la IP de la laptop en la red local, o la app no obtiene token y no pasa de la
> pantalla de inicio. Ambos tienen que estar en el mismo WiFi.

Notas de build:

- `local.properties` con la ruta del SDK lo genera Android Studio; si compilas
  solo por consola, crea el archivo con `sdk.dir=/ruta/al/Android/Sdk` o exporta
  `ANDROID_HOME`. Está git-ignorado a propósito.
- El release está firmado con la **debug key** → sideload, no Play Store. Para
  firmar con la tuya, pon `ELB_KEYSTORE`, `ELB_KEYSTORE_PASSWORD`,
  `ELB_KEY_ALIAS`, `ELB_KEY_PASSWORD` en `elb.properties`.
- La primera build baja LiveKit desde JitPack; requiere red.

---

## 7. Guion de demo (2 minutos)

**Antes de empezar:** web corriendo, agente corriendo, APK instalado, teléfono y
laptop en el mismo WiFi, volumen alto y auriculares en al menos un lado (dos
dispositivos con altavoz en la misma mesa = eco).

1. **Laptop** → abre `http://localhost:3000/operator`.
2. Escribe la sala: **`elb-demo`**. Idioma del despachador: español. Conectar.
3. **Teléfono** → abre la app → Ajustes → misma sala (`elb-demo`), elige el
   idioma del llamante (inglés) y tu nombre. Volver.
4. Pulsa **LLAMAR**. Acepta micrófono, ubicación y notificaciones.
5. Habla en inglés al teléfono. En la consola aparece la transcripción y se
   escucha la voz en español; los términos críticos salen resaltados.
6. Responde en español desde la consola: el teléfono lo escucha en inglés, con
   **otra voz** (las dos direcciones usan voces distintas a propósito).
7. Di algo con un término crítico ("she is not breathing") → se dispara el
   **fast-path** de notificación temprana y los contactos reciben el aviso sin
   esperar los 12 s.
8. Cuelga. El agente genera el reporte final; el link firmado abre `/r/<token>`
   y muestra **solo lo que corresponde al scope** de quien lo recibió.

Frases útiles para la demo (llamante en inglés):

> "There's been a car accident on Seventh Avenue with Forty-Fifth street."
> "My son is trapped in the back seat, he is not breathing."

---

## 8. Smoke test, en este orden

Estado real al **2026-08-22 16:45** — los pasos 1 y 2 ya se corrieron con
credenciales de verdad:

| # | Paso | Estado |
|---|---|---|
| 1 | `web` levanta y `/api/token` devuelve un JWT | ✅ HTTP 200, `identity=operator`, sala `elb-demo` |
| 2a | El worker se registra en LiveKit Cloud | ✅ `registered worker`, `elb-interpreter`, región US East B |
| 2b | El agente entra a la sala y publica sus dos tracks | ✅ `both interpretation directions are live` |
| 3 | Audio en una dirección: el llamante habla inglés, el despachador oye español | ✅ ver abajo |
| 4 | Sin cruce de audio: el operador no se suscribe al track del llamante | ✅ verificado por el harness |
| 5 | Colgar → reporte generado | ✅ dos `.txt` (operador y familia) en `reports/` |
| 5b | El link `/r/<token>` abre el reporte | ⬜ necesita Supabase: el visor lee de ahí |
| 6 | WhatsApp: opt-in del contacto y luego un aviso real | ⬜ falta plantilla aprobada |

### Cómo reproducirlo sin teléfono ni navegador

Con la web y el agente corriendo:

```bash
cd agent && PYTHONPATH=src py -3.13 ../scripts/e2e_call.py
```

El harness sintetiza la voz del llamante con ElevenLabs, entra a la sala como
`caller` **y** como `operator`, graba lo que oye el despachador y se lo manda a
Deepgram. Sale con código 0 solo si hubo traducción y **no** hubo cruce de
audio. Corrida real del 2026-08-22:

```
said  (caller, en): Help! There has been a car accident. My son is trapped in
                    the back seat and he is not breathing. We are on Seventh
                    Avenue with Forty Fifth street.
heard (operator, es): Ha habido un accidente de coche. Mi hijo está atrapado en
                    el asiento trasero y no respira. Estamos en 7th Avenue con
                    45th Street.
latency end-of-speech -> first interpreted audio: 2.33s
crosstalk: no
```

**Latencia medida:** 2,3 s y 4,2 s en dos corridas de punta a punta; la parte de
traducción sola es de **938 ms de mediana** (sale en la sección 6 del reporte).
El objetivo de 1–2 s del brief **no se cumple**: lo que se mide es 2–4 s, y la
mayor parte no es el modelo sino el endpointing del turno más el TTS.

Proveedores verificados uno por uno contra su API real:

| Proveedor | Prueba que se corrió | Resultado |
|---|---|---|
| LiveKit | `ListRooms` firmado con la key+secret | ✅ HTTP 200 |
| Deepgram | transcripción real con `nova-3`, `language=multi` | ✅ confianza 0.999 |
| Anthropic | `messages` con `claude-haiku-4-5` | ✅ HTTP 200 |
| ElevenLabs | TTS con `eleven_flash_v2_5` | ❌ 401: la API key no tiene el permiso `text_to_speech` |

---

## 9. Comandos de verificación que funcionan hoy

```bash
# Tests del agente, sin credenciales
cd agent && PYTHONPATH=src py -3.13 -m pytest tests/ -q            # 31 passed

# El agente importa contra livekit-agents 1.7 real
cd agent && PYTHONPATH=src py -3.13 -c "import elb.main; print('OK')"

# Ver un reporte .txt de ejemplo (scope familiar) con datos de prueba
cd agent && PYTHONPATH=src:tests py -3.13 -c "import sys; sys.path[:0]=['src','tests']; from test_core import _sample_report; from elb.report import build_report, Scope; print(build_report(_sample_report(), Scope.FAMILY, 'es'))"

# Web
cd web && npx tsc --noEmit && npm run build

# Android
cd android && ./gradlew assembleDebug
```

En consola de Windows, antes de imprimir texto con acentos:

```bash
export PYTHONIOENCODING=utf-8      # PowerShell: $env:PYTHONIOENCODING="utf-8"
```

---

## 10. Troubleshooting

| Síntoma | Causa | Arreglo |
|---|---|---|
| `TypeError: Invalid http_client argument; Expected an instance of httpx2.AsyncClient` | `livekit-plugins-anthropic` le pasa un cliente `httpx` al SDK `anthropic` 1.x | Ya está resuelto inyectando el cliente en `main.py`. **No quites ese workaround** ([D9](docs/DECISIONS.md#d9--el-cliente-de-anthropic-se-inyecta-a-mano-workaround-obligatorio)) |
| `livekit-agents requires Python >= 3.10` | `python` es 3.9 | usa `py -3.13` |
| `ValueError: ws_url is required, or set LIVEKIT_URL` | el proceso no ve `agent/.env` | `main.py` ya llama a `load_dotenv()` antes de importar `.config`. Si sigue pasando, estás corriendo desde otra carpeta: el `.env` se busca desde el cwd |
| El agente se registra pero **nunca entra a la sala** | `agent_name` desactiva el dispatch automático de LiveKit | el token debe llevar `RoomConfiguration` + `RoomAgentDispatch` (ya está en `/api/token`). Si cambias el `agent_name` en `main.py`, cambia también `AGENT_NAME` ahí |
| **Todos** los turnos caen en `translation exceeded 3.5s` | se le pasa `temperature` al plugin, y el SDK `anthropic` 1.x lo rechaza; LiveKit reintenta con backoff hasta agotar el presupuesto | no pasar `temperature` a `anthropic.LLM(...)` |
| `FFI Panic: timed out waiting for ReadyForRoomEventRequest` y el worker muere | se conectó una `rtc.Room` y acto seguido se cargó un modelo síncrono, bloqueando el event loop | conectar `room_b` **después** de cargar VAD y turn detector (así está en `main.py`) |
| ElevenLabs 401 `missing the permission text_to_speech` | la API key se creó con acceso restringido | elevenlabs.io → Profile → API Keys → editar la key → habilitar **Text to Speech** (y Voices → Read), o crear una con acceso completo |
| Link de reporte da 401 / firma inválida | `ELB_REPORT_SIGNING_SECRET` distinto entre agente y web | igualarlos y reiniciar **ambos** |
| El agente no dispara notificaciones (401 en `/api/notify`) | `ELB_INTERNAL_TOKEN` distinto | igualarlo en los dos `.env` |
| Cada lado oye su propia interpretación | el cliente se suscribió a todo | `autoSubscribe = false` + filtrar por nombre de track ([D5](docs/DECISIONS.md#d5--dos-agentsession-en-un-proceso-con-dos-conexiones-y-tracks-nombrados)) |
| La app no pasa de la pantalla de inicio | `ELB_API_BASE` apunta a `10.0.2.2` en un teléfono físico | poner la IP de la laptop en `android/elb.properties` y recompilar |
| Gradle no resuelve `com.github.davidliu:audioswitch` | falta JitPack | ya está en `settings.gradle.kts`; revisa la red/proxy |
| `Could not find plugin org.jetbrains.kotlin.android` | AGP 9 trae Kotlin integrado | ese plugin **no** debe aplicarse ([D14](docs/DECISIONS.md#d14--decisiones-de-build-de-android-que-parecen-errores-y-no-lo-son)) |
| Build de Android falla con `error writing value of type DefaultConfigurableFileCollection` | configuration cache de Gradle | está desactivada a propósito en `gradle.properties` |
| Meta no valida el webhook | `localhost` no es alcanzable | `ngrok http 3000` y usar esa URL |
| Envíos de WhatsApp devuelven `skipped: true` | falta `WHATSAPP_PHONE_NUMBER_ID` o la credencial | ver [`docs/WHATSAPP_TEMPLATE.md`](docs/WHATSAPP_TEMPLATE.md) §8 |
| `UnicodeEncodeError: 'cp932'` al imprimir | consola de Windows | `PYTHONIOENCODING=utf-8` |

---

## 11. Lo que este runbook **no** puede prometer

Al 2026-08-22 hay credenciales de LiveKit, Deepgram y Anthropic funcionando, y
los pasos 1–2 del smoke test están verdes (§8). Lo que **sigue sin ejecutarse**:
una llamada con audio real de punta a punta, la latencia medida (objetivo
1–2 s, realista 1,5–3 s), el envío por WhatsApp y la escritura a Supabase. Los
números de latencia del reporte de ejemplo salen de datos de prueba, no de una
llamada.

El bloqueo inmediato es la key de ElevenLabs sin el permiso `text_to_speech`:
sin TTS el agente transcribe y traduce, pero no sale voz.

Además, el push nativo (`/api/notify` con FCM) está implementado del lado del
servidor pero la app Android **no registra token FCM todavía**: hoy el camino de
"contacto con la app instalada" cae igualmente a WhatsApp.
