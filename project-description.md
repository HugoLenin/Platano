# AuXio

**Un intérprete simultáneo entre quien llama al 123 y quien contesta — y un
reporte que le llega a su familia sin contarles de más.**

---

## El problema

Cuando una persona que no habla español tiene una emergencia en Colombia, la
llamada al 123 se convierte en el cuello de botella. El operador no entiende la
dirección, el llamante no entiende la pregunta, y los minutos que se pierden
ahí son minutos de ambulancia.

Las soluciones actuales son un intérprete humano por teléfono (si hay uno
disponible, para ese idioma, a esa hora) o un traductor genérico que **no sabe
que hay palabras que no puede equivocar**. Un traductor genérico que se come un
"no" convierte "no está respirando" en "está respirando". Ese error no es un
error de idioma: es un cambio de prioridad de despacho.

Y hay un segundo problema que casi nadie atiende: cuando todo termina, la
familia se entera horas después, por teléfono, sin saber qué pasó ni dónde.

## Qué hicimos

Un sistema de tres piezas que conversan en una sola sala de audio:

- **App Android** para quien llama, en su idioma.
- **Consola web** para el despachador, en español.
- **Agente intérprete** que entra a la llamada como un tercer participante y
  traduce en las dos direcciones, en tiempo real.

Cada dirección viaja en su **propio track de audio con nombre propio**, y cada
cliente se suscribe únicamente al suyo. Nadie escucha la interpretación
destinada al otro.

## Las tres cosas que lo hacen distinto

### 1. Hay palabras que el modelo no puede perder

Un glosario compartido de **19 términos críticos en 6 idiomas** (es/en/pt/fr/
de/it) viaja idéntico al agente, a la web y al teléfono. Después de cada
traducción, un verificador comprueba que sobrevivieron.

Solo interviene en dos casos: cuando falta un término de severidad crítica, o
cuando **se invirtió la polaridad** ("is breathing" → "no respira"). No fuerza
el literal para todo, porque "my son" → "mi hijo" ya es una buena traducción y
llenar la pantalla de mayúsculas entrena al despachador a ignorarlas.

Si la traducción se pasa del presupuesto de latencia, el sistema **habla el
original** en vez de dejar el turno en silencio. Una frase sin traducir es
recuperable; un silencio de cinco segundos en una emergencia no.

### 2. Se avisa a la familia mientras la llamada sigue

En paralelo a la interpretación corre una extracción estructurada de qué pasó,
dónde y a cuántas personas. Cuando la ubicación y el tipo de emergencia son
**estables y confiables** (y no antes), sale el aviso a los contactos de
confianza — sin esperar a que la llamada termine.

Si aparece un término crítico como *no respira*, *arma*, *incendio* o
*atrapado*, se activa un camino rápido que baja los umbrales: ahí esperar a
confirmar es demora, no prudencia.

### 3. El familiar y el operador no reciben el mismo reporte

Al colgar se generan **dos reportes distintos**, no uno filtrado:

| | Despachador | Familiar |
|---|---|---|
| Qué pasó, dónde, cuántas personas, peligros | ✅ | ✅ |
| Condiciones clínicas, confianzas, transcripción completa | ✅ | ❌ |

El de familia se genera por separado, así que los campos que no le
corresponden **nunca se escriben** en él: un bug de renderizado no puede
filtrarlos. El enlace es un token firmado (HMAC-SHA256) que carga su propio
alcance, expira solo y se puede revocar sin romper los demás.

Y el aviso por WhatsApp se manda solo a quien **dio consentimiento explícito**:
el contacto tiene que escribirle una vez a nuestro número. Ese único mensaje es
a la vez el permiso auditable y lo que habilita el envío.

## Lo que este sistema NO hace, a propósito

- **No da instrucciones médicas.** Todos los reportes lo dicen en la primera
  pantalla: *informa, no instruye*. No reemplaza al 123.
- **No rastrea a nadie.** La ubicación se lee únicamente mientras hay una
  llamada activa. Nunca pedimos el permiso de ubicación en segundo plano.
- **No guarda los contactos en el teléfono.** Viven en el servidor, porque la
  premisa del producto es que sobrevivan a perder el teléfono.

## Stack

LiveKit (audio en tiempo real) · Deepgram Nova-3 con detección multilingüe
(STT) · **Claude Haiku 4.5** para traducir y extraer, **Claude Opus 5** para el
reporte final · ElevenLabs Flash v2.5 (TTS, una voz distinta por dirección) ·
Next.js 16 · Android nativo con Jetpack Compose · Supabase · WhatsApp Cloud API
vía Kapso.

## Estado

Construido y verificado ejecutándose: **APK real** (`assembleDebug` y
`assembleRelease`, con el glosario dentro), consola web compilando limpia, 31
tests del agente en verde e interoperabilidad de firmas Python↔TypeScript
probada — incluyendo que una falsificación que intenta subir de alcance
*familiar* a *operador* es rechazada.

Y funciona de punta a punta: en una llamada real el llamante dijo en inglés
*"my son is trapped in the back seat and he is not breathing"* y el despachador
escuchó *"mi hijo está atrapado en el asiento trasero y no respira"*, **2,3
segundos** después de que terminara de hablar. El reporte se generó solo, con la
ubicación extraída y los términos críticos marcados.

Lo que falta es operacional: el visor de reportes necesita la base de datos
conectada y el aviso por WhatsApp necesita la plantilla aprobada por Meta. Todo
el estado, sin maquillaje, está en el repo.
