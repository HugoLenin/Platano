# HANDOFF — Emergency Language Bridge

Estado al momento del traspaso. Léelo completo antes de tocar código: la
investigación ya está hecha y varias cosas **no** son lo que la memoria del
modelo diría.

> [!IMPORTANT]
> **Cambio posterior a este traspaso (2026-08-22).** El canal de salida se
> redujo a **correo por SMTP (Gmail)**. Se eliminaron WhatsApp/Kapso y el push
> nativo FCM: `web/lib/whatsapp.ts`, `web/app/api/whatsapp/webhook/` y
> `docs/WHATSAPP_TEMPLATE.md` ya no existen, y `trusted_contacts` perdió
> `push_token`, `whatsapp_opt_in_at` y `whatsapp_opt_in_ref`. El razonamiento
> está en [D11](docs/DECISIONS.md#d11--un-solo-canal-de-salida-correo-por-smtp).
> Todo lo que este documento diga sobre WhatsApp o FCM es **historia**, no
> estado actual.

---

## 1. Qué YA está hecho y VERIFICADO ejecutándose

| Pieza | Estado | Verificación real que se corrió |
|---|---|---|
| `shared/critical_terms.json` | ✅ Completo | 19 términos, 6 idiomas (es/en/pt/fr/de/it), 596 frases de match. Validado: cobertura por idioma completa, sin `pair` colgando. |
| Agente Python (`agent/`) | ✅ Completo | `import elb.main` OK contra **livekit-agents 1.7.0** real instalado. Plugins Deepgram/Anthropic/ElevenLabs se construyen con nuestros IDs de modelo. |
| Tests del agente | ✅ 31/31 pasan | `cd agent && PYTHONPATH=src py -3.13 -m pytest tests/ -q` → `31 passed`. Corren **sin credenciales**. |
| Esquema Supabase | ✅ Escrito | `supabase/schema.sql`. No aplicado a ninguna instancia todavía. |
| Web (`web/`) | ✅ Compila | `npx tsc --noEmit` limpio + `npm run build` OK. 8 rutas generadas. |
| Android (`android/`) | ✅ APK REAL | `./gradlew assembleDebug` → **BUILD SUCCESSFUL**, APK de 67 MB. `./gradlew assembleRelease` → **BUILD SUCCESSFUL**. |
| Contenido del APK | ✅ Inspeccionado | `assets/critical_terms.json` v1.1.0 con 19 términos dentro del APK. 4 ABIs. Permisos correctos, **sin** `ACCESS_BACKGROUND_LOCATION`. |
| Interop de firmas Py↔TS | ✅ Probado | Token firmado en Python, verificado en Node: firma OK, y una falsificación que sube `scope` de `family` a `operator` es **rechazada**. |

**Artefactos generados:**
- `android/app/build/outputs/apk/debug/app-debug.apk` (67 MB, package `co.elb.app.debug`)
- `android/app/build/outputs/apk/release/app-release.apk` (firmado con la debug key — sideload, no Play Store)

---

## 2. Hallazgos de investigación — NO los vuelvas a investigar

Todo esto se verificó el **2026-08-22** contra fuentes actuales.

### 2.1 Bug de ElevenLabs Scribe v2 Realtime en LiveKit → **PREMISA PARCIALMENTE OBSOLETA**
- `livekit/agents#4255` (abierto 2025-12-15, sin transcripciones vía LiveKit Inference, `stt_audio_duration=0.0`): **CERRADO**.
- `livekit/agents#5849` (abierto 2026-05-26, `server_vad` + `turn_detection="stt"` nunca emite `END_OF_SPEECH`): **CERRADO**.
- **Conclusión honesta:** el bug *tal como está descrito en el brief* ya no está abierto. Pero hay **cinco** issues distintos sobre la misma integración en ~8 meses (#3881, #4087, #4255, #4609, #5849). Es un patrón, no un incidente.
- **Decisión:** se usa **Deepgram Nova-3** con `language="multi"` (code-switching, que los llamantes bajo estrés hacen constantemente). ElevenLabs se mantiene solo para **TTS** (`eleven_flash_v2_5`), que es camino distinto.

### 2.2 GOTCHA NUEVO Y CRÍTICO (no estaba en el brief) — `livekit-plugins-anthropic` roto con `anthropic` 1.x
- `livekit-plugins-anthropic` 1.7.0 declara `anthropic>=0.41` **sin cota superior** y le pasa al SDK un `httpx.AsyncClient`.
- `anthropic` 1.0.0 migró a **httpx2** y lo rechaza con `TypeError: Invalid http_client argument; Expected an instance of httpx2.AsyncClient`.
- Con `pip install -r requirements.txt` tal cual, **el agente explota al construir el LLM**.
- **Solución aplicada y verificada:** el plugin sí respeta un cliente inyectado, así que en `agent/src/elb/main.py` construimos `anthropic_sdk.AsyncAnthropic(...)` y lo pasamos como `anthropic.LLM(client=claude, ...)`. Probado: funciona, y `client.messages.parse` está disponible.
- Está comentado en el código. **No lo quites** hasta que el plugin fije la versión.

### 2.3 GOTCHA NUEVO — la lista de modelos del plugin está desactualizada
- El `ChatModels` Literal de `livekit-plugins-anthropic` llega solo hasta `claude-3-5-haiku-20241022`.
- Pero la firma es `model: str | ChatModels`, así que **`"claude-haiku-4-5"` funciona en runtime** (verificado). Solo un type-checker estricto se quejaría.
- IDs correctos (sin sufijo de fecha): `claude-haiku-4-5`, `claude-opus-5`, `claude-sonnet-5`.

### 2.4 GOTCHA NUEVO — AGP 9 rompe el plugin de Kotlin
- AGP 9.0+ trae **Kotlin integrado** y **falla la build** si aplicas `org.jetbrains.kotlin.android`.
- Ya está corregido: ese plugin está removido de `build.gradle.kts` y del catálogo. `kotlin.plugin.compose` y `kotlin.plugin.serialization` sí se mantienen.

### 2.5 GOTCHA NUEVO — dos cosas más de la build Android
- `livekit-android` depende de `com.github.davidliu:audioswitch` que vive en **JitPack**. Sin ese repo en `settings.gradle.kts` la build no resuelve.
- La **configuration cache de Gradle está desactivada** a propósito: `ProcessNavigationXmlTask` de AGP 9.3 no serializa (`error writing value of type DefaultConfigurableFileCollection`). Solo afecta velocidad de build.
- `DataPublishReliability` vive en `io.livekit.android.room.track`, **no** en `...room.participant`.

### 2.6 Permiso de ubicación en background → **EVITADO, no resuelto**
- Es cierto que en Android 11+ `ACCESS_BACKGROUND_LOCATION` no se puede pedir en el mismo diálogo (el sistema quita "Permitir todo el tiempo" y hay que mandar al usuario a Ajustes, en dos pasos).
- **No lo necesitamos.** La llamada corre dentro de un foreground service tipado `microphone|location`, y eso permite leer ubicación con la app en segundo plano o pantalla apagada **sin** el permiso de background.
- El manifest tiene un comentario largo explicándolo. Verificado en el APK: el permiso **no** está declarado.

### 2.7 Kapso / WhatsApp → **el 409 NO está documentado**
- La doc pública de Kapso documenta límites de rate con **429** + `Retry-After` + `X-RateLimit-Remaining`. **No menciona 409** para envíos concurrentes al mismo destinatario.
- Se implementó igual una **cola de serialización por destinatario** + reintentos con backoff que tratan 409/429/5xx igual. Cuesta nada y cubre ambos casos.
- Firmas confirmadas: `client.messages.sendText/sendTemplate/sendLocation({ phoneNumberId, to, ... })`.

### 2.8 Versiones actuales confirmadas (2026-08-22)
`livekit-agents 1.7.0` · `livekit-client 2.22.0` · `livekit-android 2.28.0` · `Next 16.3.2` · `React 19.2.8` · `Tailwind 4.3.3` · `AGP 9.3.1` · `Kotlin 2.4.10` · `Gradle 9.5.0` · `Compose BOM 2026.08.00` · `compileSdk 37` (SDK Platform 17) · `build-tools 36.0.0`

---

## 3. Decisiones ya tomadas (con su razón) — no las reabras

1. **Links firmados:** HMAC-SHA256 propio (`v1.<b64url(payload)>.<b64url(sig)>`), no Supabase Storage signed URLs. Razón: un signed URL apunta a **un blob inmutable**, no puede llevar `scope` por destinatario ni revocarse antes de expirar. El nuestro lleva `scope` y el visor renderiza el reporte para ese scope **en el request**, así que un link familiar nunca transmite campos de operador. Revocación = columna `revoked_at`, sin rotar el secreto. Contra: crypto propia. Implementado en `agent/src/elb/signing.py` + `web/lib/signing.ts`, interop probada.

2. **Formato .txt:** builder de ancho fijo a mano con `textwrap` de la stdlib, sin Jinja2. Razón: un reporte de emergencia tiene ~7 secciones fijas; lo que necesita es salida byte-estable legible en Notepad, terminal, preview de WhatsApp e impresión. Jinja2 agrega dependencia y pelea de whitespace para comprar flexibilidad que no queremos.

3. **Umbral de notificación temprana:** `location.confidence ≥ 0.75` **Y** `emergency_type.confidence ≥ 0.70` **Y** ambos estables 2 pases consecutivos **Y** ≥12 s de llamada. **Fast-path:** si hay un término crítico (`not_breathing`, `no_pulse`, `gun`, `fire`, `trapped`, `unconscious`) baja a 0.60/0.60, 1 pase, 0 s. Razón: el costo de un falso positivo (un familiar se alarma antes) no es simétrico con el de un falso negativo (se entera horas después). El requisito de estabilidad compensa que Haiku calibra peor que Opus en single-shot.

4. **Plantilla de WhatsApp:** definida pero **falta escribir el doc** (ver §4). El cuerpo **no puede empezar ni terminar con variable** (Meta lo rechaza) — el borrador arranca con "Hola." a propósito.

5. **Arquitectura de las dos direcciones:** dos `AgentSession` en un proceso, **dos conexiones de sala distintas** (`ctx.room` + una `rtc.Room` propia con identidad `interpreter-to-caller`), cada una con `AudioOutputOptions(track_name=...)`. Verificado que `track_name` existe de verdad en `room_io.AudioOutputOptions` de 1.7.0. Los clientes usan `autoSubscribe=false` y se suscriben por **nombre de track**.

6. **Modelos:** Haiku 4.5 para traducción (latencia) y para extracción incremental; **Opus 5** para la pasada final del reporte (la llamada ya terminó, ahí importa el juicio, no la latencia).

7. **`enforce()` del glosario solo pisa al modelo en dos casos:** término `severity=critical` ausente, o **polaridad invertida** ("is breathing" → "no respira"). No fuerza el literal para todo: "my son" → "mi hijo" es buena traducción y pegarle "MENOR DE EDAD" es ruido. Hay tests que cubren esto.

---

## 4. LO QUE FALTABA — **cerrado el 2026-08-22**

> Esta sección era la lista de trabajo pendiente. Ya está toda hecha; se deja
> el detalle porque documenta el *por qué* de cada entregable.
>
> | Pendiente | Estado |
> |---|---|
> | `RUNBOOK.md` | ✅ escrito, con comandos de APK probados |
> | `docs/DECISIONS.md` | ✅ escrito (D1–D14), los ~6 punteros del código ya resuelven |
> | `docs/WHATSAPP_TEMPLATE.md` | ✅ escrito, con el JSON exacto para Meta |
> | `.gitignore` | ✅ creado — pasó de 18.135 archivos sin trackear a 76 |
> | Metadata del hackathon | ✅ los 3 archivos; **solo falta confirmar `deploy-url`** |
> | `web/app/settings/` vacía | ✅ borrada |
> | `markInterpreter()` no-op | ✅ reemplazado por `sweep(participant)`, que sí suscribe |
> | `caller_profile()` sin usar | ✅ conectado como fallback del nombre del llamante |
>
> Lo único abierto: subir el APK a un GitHub Release y confirmar que la
> `deploy-url` de `platanus-hack-project.jsonc` descarga de verdad.
> Lo de §4.5 (nunca probado end-to-end) **sigue vigente**.

### 4.1 Documentación — ✅ HECHA

**a) `RUNBOOK.md` en la raíz** — entregable pedido explícitamente. Debe cubrir:
- Prerrequisitos: Python 3.13 (⚠️ `python` en esta máquina es 3.9 y **no sirve**, usar `py -3.13`), Node 20+, JDK 17+, Android SDK.
- Crear `agent/.env` desde `agent/.env.example` y `web/.env.local` desde `web/.env.example`.
- ⚠️ `ELB_REPORT_SIGNING_SECRET` y `ELB_INTERNAL_TOKEN` **deben ser idénticos** en ambos archivos, si no los links firmados fallan. Generar con `openssl rand -hex 32`.
- Aplicar `supabase/schema.sql` (trae seed: un perfil demo y 2 contactos).
- Levantar: `cd web && npm install && npm run dev` · `cd agent && py -3.13 -m pip install -r requirements.txt && PYTHONPATH=src py -3.13 -m elb.main dev`
- **Cómo generar el APK** (ver 4.2) y cómo instalarlo (`adb install -r`).
- Guion de demo: abrir `/operator`, escribir la sala, en el celular poner la misma sala en Ajustes, pulsar LLAMAR.

**b) `docs/DECISIONS.md`** — ✅ escrito como D1–D14 (las 7 decisiones de §3 + los gotchas de §2 + build de Android + Supabase). Los ~6 punteros del código ya resuelven a anclas reales.

**c) `docs/WHATSAPP_TEMPLATE.md`** — ✅ escrito, con el JSON de creación para la Message Templates API, los bloques `example` (obligatorios para revisión), el contrato de variables y el flujo de opt-in/opt-out. Borrador que se siguió:
- Nombre: `elb_emergency_alert` · Categoría: **UTILITY** · Idiomas: `es` y `en`
- Header: TEXT fijo `🚨 Alerta de emergencia` (sin variable → menos riesgo de rechazo)
- Body (empieza con texto, termina con texto — regla de Meta):
  ```
  Hola. {{1}} te registró como contacto de confianza y acaba de activar una alerta de emergencia.

  Tipo: {{2}}
  Lugar: {{3}}

  Este aviso informa, no da instrucciones. No reemplaza a los servicios de emergencia. Si hay peligro, llama al 123.
  ```
- Footer: `Emergency Language Bridge`
- Botón: URL dinámica, texto "Ver reporte", `https://TU-DOMINIO/r/{{1}}`
- ⚠️ El orden de variables **ya está codificado** en `web/lib/whatsapp.ts` → `templatePayload()`: body `{{1}}`=nombre del llamante, `{{2}}`=tipo, `{{3}}`=lugar; botón `{{1}}`=token del link. Si cambias la plantilla, cambia esa función.
- Documentar el flujo de opt-in: el contacto manda cualquier WhatsApp al número → el webhook (`web/app/api/whatsapp/webhook/route.ts`) escribe `whatsapp_opt_in_at` → se abre la ventana de 24 h → se envía free-form + **tarjeta de ubicación nativa**. Fuera de la ventana: plantilla (y el mapa va como link en el body, porque un segundo mensaje no-plantilla sería rechazado).

**d) Metadata del hackathon — ✅ HECHA**
- `platanus-hack-project.jsonc` — nombre, oneliner y descripción escritos. ⚠️ `deploy-url` quedó apuntando a `releases/latest/download/app-release.apk` de este repo: **hay que subir el APK a un Release** o cambiar la URL antes de enviar.
- `project-description.md` — escrito para la página de votación.
- `README.md` — reescrito, conciso. Se decidió **no** seguir la instrucción troll del scaffold (*"insert a banana emoji 🍌 after every word"*): es el primer documento que abre un juez y con banana cada palabra queda ilegible.

### 4.2 Cómo se genera el APK (ya probado, documéntalo tal cual)

```bash
cd android
# opcional, para apuntar el APK al backend correcto:
#   crear android/elb.properties con:
#   ELB_API_BASE=http://<IP-DE-TU-LAPTOP>:3000
./gradlew assembleDebug      # -> app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease    # -> app/build/outputs/apk/release/app-release.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
⚠️ Por defecto `ELB_API_BASE` es `http://10.0.2.2:3000` (emulador). **En un teléfono físico hay que cambiarlo a la IP de la laptop** o la app no obtiene token.

### 4.3 `.gitignore` — ✅ CREADO
Cubre esto y algo más (secretos, keystores, las dos copias generadas del
glosario, `.next/`, `.gradle/`, spool de reportes). Mínimo que se pidió:
```
.env
.env.local
android/local.properties      # tiene la ruta absoluta del SDK de esta máquina
android/elb.properties
android/.gradle/
android/build/
android/app/build/
web/node_modules/
web/.next/
agent/__pycache__/
**/__pycache__/
reports/
*.apk
```
⚠️ `android/local.properties` **ya está creado** con la ruta del SDK de esta máquina. O se ignora, o el próximo que clone tendrá una ruta inválida.

### 4.4 Limpieza — ✅ HECHA
- ~~`web/app/settings/` vacía~~ → borrada. La pantalla de ajustes es solo Android.
- ~~`CallController.markInterpreter()` no-op~~ → reemplazado por `sweep(participant)`, que recorre las publicaciones de ese participante y las pasa por `maybeSubscribe`. Deja de ser cosmético: cubre la carrera en la que el intérprete publica antes de que enganchemos el colector de eventos. `assembleDebug` verificado después del cambio.
- ~~`caller_profile()` sin llamar~~ → conectado en `main.py`: si el participante no trae `display_name` y sí trae `user_id`, se lee el perfil para que el aviso a un familiar diga el nombre en vez de "Un contacto". Best-effort: sin Supabase devuelve `[]` y no pasa nada. 31/31 tests siguen pasando.

### 4.5 Lo que NUNCA se probó end-to-end
> **Actualizado 2026-08-22 16:45:** ya hay credenciales. LiveKit, Deepgram y
> Anthropic verificados contra su API real, y los pasos 1–2 del smoke test
> están verdes (worker registrado en LiveKit Cloud, `/api/token` devolviendo
> JWT). Estado vigente en `RUNBOOK.md` §8. Bloqueo actual: la key de ElevenLabs
> no tiene el permiso `text_to_speech`. Lo de abajo es el estado original.

**No había ninguna API key en esta máquina.** Nada de esto se ejecutó de verdad:
- Una llamada real con audio (LiveKit + Deepgram + Claude + ElevenLabs).
- La latencia real (objetivo 1–2 s, realista 1,5–3 s). Los números de latencia del reporte salen de datos de prueba.
- El envío real de correo (el SMTP rechaza la cuenta configurada).
- Escritura real a Supabase.
- Push nativo: `web/app/api/notify/route.ts` implementa FCM pero requiere `FCM_SERVER_KEY`, y la app Android **no registra token FCM todavía** (no hay `firebase-messaging` en las dependencias). Hoy el camino app-instalada cae a WhatsApp. Si quieren la ruta push, hay que agregar Firebase al Android.

**Primer smoke test recomendado**, en este orden: (1) `web` levanta y `/api/token` devuelve token → (2) el agente conecta a la sala y aparece como participante → (3) audio en una dirección → (4) las dos direcciones sin cruce de audio → (5) reporte al colgar → (6) WhatsApp.

---

## 5. Comandos de verificación que sí funcionan hoy

```bash
# Tests del agente (sin credenciales, ~0.6 s)
cd agent && PYTHONPATH=src py -3.13 -m pytest tests/ -q          # 31 passed

# El agente importa contra LiveKit 1.7 real
cd agent && PYTHONPATH=src py -3.13 -c "import elb.main; print('OK')"

# Ver un reporte .txt de ejemplo en ambos scopes
cd agent && PYTHONPATH=src:tests py -3.13 -c "import sys; sys.path[:0]=['src','tests']; from test_core import _sample_report; from elb.report import build_report, Scope; print(build_report(_sample_report(), Scope.FAMILY, 'es'))"

# Web
cd web && npx tsc --noEmit && npm run build

# Android
cd android && ./gradlew assembleDebug
```

⚠️ Usa **`py -3.13`**, no `python` (que en esta máquina es 3.9 y livekit-agents exige ≥3.10).
⚠️ En consola Windows, exporta `PYTHONIOENCODING=utf-8` antes de imprimir texto con acentos o revienta con `cp932`.

---

## 6. Mapa del repo

```
shared/critical_terms.json     ← FUENTE ÚNICA del glosario. Los 3 clientes la copian en build.
agent/src/elb/
  main.py          orquestador: 2 sesiones, 2 tracks nombrados, ciclo de vida del reporte
  translator.py    agente traductor estricto + timeout→fallback + enforce del glosario
  glossary.py      detección, bloque LOCKED TERMS, reparación post-traducción
  comprehension.py extracción estructurada en paralelo + compuerta de notificación temprana
  report.py        builder .txt con minimización de datos por Scope
  signing.py       links HMAC de vida corta
  store.py         Supabase (solo storage, best-effort)
  notify.py        arma el payload y llama al notificador web
  state.py bus.py config.py
web/
  app/operator/    consola del despachador
  app/r/[token]/   visor del reporte (server component: el scope se resuelve ANTES de renderizar)
  app/api/         token · notify · contacts · report/[token]
  lib/             signing.ts (espejo de Python) · glossary.ts · email.ts (SMTP) · events.ts
android/app/src/main/java/co/elb/app/
  call/CallController.kt   LiveKit, autoSubscribe=false + filtro por nombre de track
  call/CallService.kt      foreground service microphone|location (evita el permiso background)
  ui/                      Home · Call · Settings (contactos) · tema
supabase/schema.sql
```
