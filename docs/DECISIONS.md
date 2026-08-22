# Decisiones de diseño — Emergency Language Bridge

Este archivo es el destino de los `See docs/DECISIONS.md` que aparecen en el
código (`agent/src/elb/{config,main,report,signing}.py`,
`android/.../AndroidManifest.xml`, `MainActivity.kt`, `CallService.kt`).

Cada entrada dice **qué se decidió**, **por qué**, **qué se pierde** y **dónde
vive en el código**. Si vas a cambiar una de estas cosas, lee primero el
"Contra" — casi todas ya se discutieron con la alternativa obvia enfrente.

Investigación verificada el **2026-08-22**.

---

## D1 · Links firmados propios (HMAC-SHA256), no signed URLs de Supabase

**Decisión.** El link que recibe un familiar es un token sin estado que
firmamos nosotros:

```
v1.<b64url(payload_json)>.<b64url(hmac_sha256(secret, "v1." + b64payload))>
```

El payload lleva `report_id`, `contact_id`, `scope`, `kind`, `exp`, `nonce`.

**Por qué.** Un signed URL de storage apunta a **un blob inmutable**. Eso
implica dos cosas que no aceptamos:

1. No puede llevar `scope` por destinatario. Habría que subir dos archivos y
   confiar en no confundir cuál link se manda a quién.
2. No se puede revocar antes de que expire sin rotar el secreto (y romper
   todos los demás links vivos).

Con el token nuestro, el visor recibe el `scope` firmado y **renderiza el
reporte para ese scope en el request**. Un link familiar nunca transporta
campos de operador: no es que estén ocultos en el cliente, es que no viajan.
Revocar es escribir `revoked_at` en la fila del reporte o del link.

La firma cubre el prefijo de versión (`v1.`), así que se puede rotar el
esquema sin abrir un camino de downgrade.

**Contra.** Es criptografía propia, aunque sea `hmac` de la stdlib. Se mitiga
con: comparación en tiempo constante, TTL corto (6 h para el aviso temprano,
72 h para el final) y **una prueba de interoperabilidad Python↔TypeScript que
incluye una falsificación** — subir `scope` de `family` a `operator` es
rechazado.

**Código.** `agent/src/elb/signing.py` · `web/lib/signing.ts` (espejo exacto) ·
`web/app/r/[token]/page.tsx` (server component: el scope se resuelve **antes**
de renderizar) · tabla `report_links` en `supabase/schema.sql`.

> [!WARNING]
> `ELB_REPORT_SIGNING_SECRET` **debe ser idéntico** en `agent/.env` y
> `web/.env.local`. Si no, todo link firmado por el agente es inválido para la web.

---

## D2 · Reporte en `.txt` de ancho fijo, sin motor de plantillas

**Decisión.** Builder a mano sobre `textwrap` de la stdlib. Ancho 80, sin
Jinja2, sin PDF, sin HTML como formato primario.

**Por qué.** Un registro de emergencia tiene ~7 secciones fijas. Lo que
necesita es salida **byte-estable** que se lea igual en Notepad, en una
terminal, en el preview de WhatsApp e impresa en papel. Jinja2 agrega una
dependencia y pelea de whitespace para comprar una flexibilidad que aquí es un
defecto: que el layout sea rígido es la feature.

**Contra.** Cambiar el layout es editar Python, no una plantilla.

**Código.** `agent/src/elb/report.py` (`WIDTH = 80`).

---

## D3 · La minimización de datos se aplica al renderizar, no al enviar

**Decisión.** `Scope.OPERATOR` y `Scope.FAMILY` son dos renders **generados
por separado**. El de familia no es el de operador con campos tachados.

| Campo | `OPERATOR` | `FAMILY` |
|---|---|---|
| Qué pasó, dónde, cuántas personas, peligros en sitio | ✅ | ✅ |
| Condiciones clínicas por víctima | ✅ | ❌ |
| Confianzas de extracción | ✅ | ❌ |
| Calidad de interpretación / turnos en fallback | ✅ | ❌ |
| Transcripción bilingüe completa | ✅ | ❌ |

**Por qué.** Si el reporte familiar se derivara filtrando el de operador, un
bug de renderizado filtra detalle clínico a un pariente asustado. Generándolo
por separado, los campos excluidos **nunca se escriben** en esa cadena.

Las dos variantes se guardan en la tabla `reports` (`operator_txt`,
`family_txt`) por la misma razón.

**Código.** `agent/src/elb/report.py` (`class Scope`, `build_report`).

---

## D4 · Umbral de la notificación temprana (y su fast-path)

**Decisión.** Se avisa a los contactos **durante** la llamada solo si:

```
location.confidence       >= 0.75     (ELB_NOTIFY_LOC_CONF)
emergency_type.confidence >= 0.70     (ELB_NOTIFY_TYPE_CONF)
ambos estables             2 pasadas  (ELB_NOTIFY_STABLE_PASSES)
llamada en curso          >= 12 s     (ELB_NOTIFY_MIN_ELAPSED_S)
```

**Fast-path.** Si aparece un término crítico del glosario (`not_breathing`,
`no_pulse`, `gun`, `fire`, `trapped`, `unconscious`) los umbrales bajan a
**0.60 / 0.60, 1 pasada, 0 s**.

**Por qué.** Los dos errores no cuestan lo mismo. Un falso positivo asusta a un
familiar unos minutos antes de tiempo; un falso negativo hace que se entere
horas después. Pero un falso positivo *con datos malos* ("incendio" en una
dirección equivocada) es peor que no avisar, y de ahí sale el requisito de
**estabilidad en 2 pasadas**: compensa que Haiku calibra peor que Opus en
single-shot, sin pagar la latencia de Opus.

El fast-path existe porque cuando alguien dice "no respira", esperar 12
segundos a una segunda pasada de confirmación no es prudencia, es demora.

**Contra.** Los números son un juicio de producto, no una calibración
empírica: **no se midieron contra llamadas reales**. Son todos variables de
entorno precisamente para poder ajustarlos sin tocar código.

**Código.** `agent/src/elb/config.py:75-81` ·
`agent/src/elb/comprehension.py` (`CRITICAL_FAST_PATH`, ~línea 298).

---

## D5 · Dos `AgentSession` en un proceso, con **dos conexiones** y tracks nombrados

**Decisión.** El agente entra a la sala **dos veces**: la conexión que le da
LiveKit (`ctx.room`) y una `rtc.Room` propia con identidad
`interpreter-to-caller`. Cada dirección publica un track de audio con nombre
explícito:

```
caller   --(en)-->  AgentSession A  -->  track "interpreter-to-operator"
operator --(es)-->  AgentSession B  -->  track "interpreter-to-caller"
```

**Por qué.** Cada `RoomIO` es dueño de **una** salida de audio. Meter las dos
direcciones en una conexión obliga a multiplexar esa salida; darle a cada
dirección su propio participante hace que la regla del cliente sea trivial y
difícil de romper.

**La consecuencia crítica:** los clientes **deben** conectarse con
`autoSubscribe = false` y suscribirse solo a su track. LiveKit por defecto se
suscribe a todo, y eso haría que cada lado oiga la interpretación destinada al
otro — el bug más vergonzoso posible en una demo de interpretación.

Verificado que `track_name` existe de verdad en `room_io.AudioOutputOptions` de
livekit-agents 1.7.0.

**Código.** `agent/src/elb/main.py` (docstring del módulo + `make_session`) ·
`android/.../call/CallController.kt` (`maybeSubscribe` filtra por
`p.name == TRACK_TO_CALLER`) · `web/components/useElbRoom.ts` · constantes
compartidas en `agent/src/elb/config.py`.

---

## D6 · Haiku 4.5 para traducir, Opus 5 para el reporte final

**Decisión.** `claude-haiku-4-5` para traducción y para la extracción
incremental; `claude-opus-5` para la pasada final del reporte.

**Por qué.** La traducción está en el camino crítico de una llamada de
emergencia: ahí manda la latencia. El reporte final se construye **cuando la
llamada ya terminó**; ahí no hay nadie esperando y lo que importa es el juicio
sobre un transcript completo.

**Nota de compatibilidad.** El `Literal ChatModels` de
`livekit-plugins-anthropic` 1.7.0 llega solo hasta `claude-3-5-haiku-20241022`,
pero la firma real es `model: str | ChatModels`, así que `"claude-haiku-4-5"`
**funciona en runtime** (verificado). Solo un type-checker estricto se
quejaría. Los IDs correctos van **sin sufijo de fecha**: `claude-haiku-4-5`,
`claude-sonnet-5`, `claude-opus-5`.

**Código.** `agent/src/elb/config.py` (`translate_model`, `extract_model`,
`report_model`), todos sobrescribibles por env.

---

## D7 · El glosario solo pisa al modelo en dos casos

**Decisión.** `Glossary.enforce()` **no** fuerza el literal para todos los
términos detectados. Interviene únicamente si:

1. el término es `severity=critical` y su rendering obligatorio **no está** en
   la traducción, o
2. **se invirtió la polaridad** ("is breathing" → "no respira").

La reparación es **aditiva**: nunca borra salida del modelo, agrega una
cláusula explícita.

**Por qué.** "my son" → "mi hijo" es una buena traducción; pegarle "MENOR DE
EDAD" al lado agrega ruido sin agregar información, y el ruido entrena al
despachador a ignorar el resaltado. Lo que sí mata gente es perder una
negación. Todo lo demás se deja pasar y solo se resalta en la UI.

**Código.** `agent/src/elb/glossary.py` (`enforce`, con tests que cubren
justamente los casos que **no** debe tocar) · fuente única en
`shared/critical_terms.json` (v1.1.0, 19 términos, 6 idiomas).

---

## D8 · Deepgram Nova-3 para STT, ElevenLabs solo para TTS

**Decisión.** STT con `nova-3` y `language="multi"`. ElevenLabs se queda con
TTS (`eleven_flash_v2_5`).

**Honestidad sobre la premisa.** El brief original de este proyecto asumía que
el bug de **ElevenLabs Scribe v2 Realtime** en LiveKit estaba abierto. Se
verificó: **no lo está.**

| Issue | Qué reportaba | Estado hoy |
|---|---|---|
| `livekit/agents#4255` | sin transcripciones vía LiveKit Inference, `stt_audio_duration=0.0` | **cerrado** |
| `livekit/agents#5849` | `server_vad` + `turn_detection="stt"` nunca emite `END_OF_SPEECH` | **cerrado** |

Aun así se mantiene Deepgram, por dos razones que sobreviven al cierre de esos
issues:

- Son **cinco** issues distintos sobre la misma integración en ~8 meses
  (#3881, #4087, #4255, #4609, #5849). Eso es un patrón, no un incidente, y una
  demo de emergencias no es el lugar para apostar.
- `language="multi"` de Nova-3 hace **code-switching**, que es exactamente lo
  que hace un migrante bajo estrés: arranca en su idioma y mete palabras del
  otro.

**Código.** `agent/src/elb/main.py:503` (el comentario apunta aquí).

---

## D9 · El cliente de Anthropic se inyecta a mano (workaround obligatorio)

> [!CAUTION]
> No quites esto sin verificar antes que el plugin lo arregló.

**Problema.** `livekit-plugins-anthropic` 1.7.0 declara `anthropic>=0.41` **sin
cota superior** y le pasa al SDK un `httpx.AsyncClient`. El SDK `anthropic`
1.0.0 migró a **httpx2** y lo rechaza:

```
TypeError: Invalid http_client argument; Expected an instance of httpx2.AsyncClient
```

Con un `pip install -r requirements.txt` limpio, **el agente explota al
construir el LLM**.

**Solución aplicada.** El plugin sí respeta un cliente inyectado, así que lo
construimos nosotros:

```python
claude = anthropic_sdk.AsyncAnthropic(api_key=settings.anthropic_api_key)
...
llm=anthropic.LLM(model=settings.translate_model, client=claude, ...)
```

Verificado: funciona, y `client.messages.parse` sigue disponible (lo usa la
extracción estructurada).

**Código.** `agent/src/elb/main.py:487`.

---

## D10 · Nunca pedimos `ACCESS_BACKGROUND_LOCATION`

**Decisión.** El permiso **no está declarado en el manifest**. Verificado en el
APK compilado.

**El problema que evita.** En Android 11+ el permiso de ubicación en segundo
plano no se puede pedir en el mismo diálogo que el de primer plano: el sistema
quita la opción "Permitir todo el tiempo" y hay que mandar al usuario a una
pantalla de Ajustes en un segundo paso. Pedirle eso a alguien que está
configurando una app **para una emergencia futura** es perderlo ahí mismo.

**Por qué no lo necesitamos.** La ubicación solo se lee mientras hay una
llamada activa, y la llamada corre dentro de un **foreground service tipado
`microphone|location`**. Un foreground service con el tipo `location` puede
leer ubicación con la app en segundo plano o la pantalla bloqueada **sin** el
permiso de background.

**Contra.** No hay ubicación cuando no hay llamada. Es exactamente lo que
queremos: no somos una app de tracking.

**Código.** `android/app/src/main/AndroidManifest.xml` (comentario largo en el
bloque de permisos) · `call/CallService.kt` · `MainActivity.kt:67`.

---

## D11 · WhatsApp: la plantilla es el camino por defecto, no el fallback

**Decisión.** Tres reglas de la plataforma mandan sobre el diseño:

1. **Ventana de 24 h.** Fuera de ella solo se puede mandar una plantilla
   aprobada por Meta. Un contacto de confianza **nunca** nos escribió, así que
   la plantilla es el camino **normal**.
2. **El opt-in es la mitigación y el consentimiento a la vez.** El contacto
   manda un WhatsApp cualquiera a nuestro número: eso abre la ventana **y** deja
   consentimiento auditable (`whatsapp_opt_in_at`, escrito por el webhook
   entrante). No es un truco de demo, es como debería funcionar en producción.
3. **Tarjeta de ubicación nativa** (`location`) en vez de un link a Google Maps
   — pero solo dentro de la ventana, porque fuera de ella un segundo mensaje
   no-plantilla sería rechazado. Por eso el cuerpo de la plantilla lleva el
   link de mapa embebido.

**Cola por destinatario.** Todos los envíos a un mismo número se serializan y
se reintentan con backoff exponencial tratando **409, 429 y 5xx** igual.

Nota de investigación: **el 409 no está documentado por Kapso.** Su doc pública
documenta rate limiting con **429 + `Retry-After` + `X-RateLimit-Remaining`**.
La cola se implementó igual porque cuesta nada y cubre ambos casos, y porque
una tormenta de reintentos contra una persona en una emergencia es el peor modo
de falla posible.

Firmas confirmadas del SDK:
`client.messages.sendText / sendTemplate / sendLocation({ phoneNumberId, to, ... })`.

**Código.** `web/lib/whatsapp.ts` · `web/app/api/whatsapp/webhook/route.ts` ·
plantilla exacta en [`docs/WHATSAPP_TEMPLATE.md`](./WHATSAPP_TEMPLATE.md).

---

## D12 · Un glosario, tres clientes, copiado en build

**Decisión.** `shared/critical_terms.json` es la **única** fuente. Se copia
automáticamente:

- a `web/lib/critical_terms.json` por `web/scripts/sync-glossary.mjs` (corre en
  `predev` y `prebuild`),
- a `android/app/src/main/assets/critical_terms.json` por la tarea Gradle
  `syncGlossary` (`dependsOn` de `preBuild`).

Las dos copias están en `.gitignore`. **No las edites**: se sobrescriben en
cada build.

**Por qué.** Si el teléfono resalta un conjunto de términos distinto al que el
intérprete realmente garantiza, la UI está mintiendo sobre la garantía.

---

## D13 · Supabase es solo almacenamiento, y best-effort

**Decisión.** La señalización de la llamada **nunca** toca la base de datos —
eso es trabajo de LiveKit. Supabase guarda lo que se configura antes de una
emergencia (`profiles`, `trusted_contacts`) y lo que se escribe durante o
después (`calls`, `transcripts`, `events`, `metrics`, `reports`,
`report_links`, `deliveries`). Todas las escrituras del agente son
best-effort: si Supabase se cae, la llamada sigue.

RLS está **activo en todas las tablas** aunque el agente y las rutas API usen
la service-role key y la salten. Existe para que agregar un cliente de usuario
final después sea seguro por defecto, no abierto por defecto.

`deliveries` tiene `unique (report_id, contact_id, kind, channel)`: es la
guarda de idempotencia que evita que un reintento notifique dos veces a un
familiar asustado.

**Código.** `supabase/schema.sql` · `agent/src/elb/store.py`.

---

## D14 · Decisiones de build de Android que parecen errores y no lo son

Todas verificadas contra AGP 9.3.1 / Kotlin 2.4.10 / Gradle 9.5.0:

- **`org.jetbrains.kotlin.android` está deliberadamente ausente.** AGP 9.0+ trae
  Kotlin integrado y **falla la build** si aplicas el plugin viejo.
  `kotlin.plugin.compose` y `kotlin.plugin.serialization` sí se mantienen.
  (`android/gradle/libs.versions.toml`)
- **JitPack está en `settings.gradle.kts`** porque `livekit-android` depende de
  `com.github.davidliu:audioswitch`, que no vive en Maven Central. Sin ese repo
  la build no resuelve.
- **La configuration cache de Gradle está desactivada a propósito.**
  `ProcessNavigationXmlTask` de AGP 9.3 no serializa (`error writing value of
  type DefaultConfigurableFileCollection`). Solo afecta la velocidad de build.
  (`android/gradle.properties`)
- **R8 está apagado en release.** Las reglas de reflexión de WebRTC + LiveKit
  son una fuente clásica de "funciona en debug, crashea en release", y un APK
  para sideload no gana nada con ser más chico.
- **`DataPublishReliability` vive en `io.livekit.android.room.track`**, no en
  `...room.participant`. Si el import falla, es esto.

---

## Versiones confirmadas (2026-08-22)

`livekit-agents 1.7.0` · `livekit-client 2.22.0` · `livekit-android 2.28.0` ·
`Next 16.3.2` · `React 19.2.8` · `Tailwind 4.3.3` · `AGP 9.3.1` ·
`Kotlin 2.4.10` · `Gradle 9.5.0` · `Compose BOM 2026.08.00` · `compileSdk 37` ·
`build-tools 36.0.0` · `minSdk 26`

---

## Lo que este documento **no** dice

Nada de esto se probó end-to-end con credenciales reales: no hubo llamada con
audio real, ni latencia medida, ni WhatsApp enviado, ni escritura real a
Supabase. Las decisiones de arriba son de diseño y están verificadas a nivel de
compilación, tests e interoperabilidad de firmas — no de operación. Ver
`HANDOFF.md` §4.5 y `RUNBOOK.md` § "Smoke test" para el orden recomendado.
