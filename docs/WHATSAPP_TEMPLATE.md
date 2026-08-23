# Plantilla de WhatsApp — `elb_emergency_alert`

Todo lo que hay que someter a Meta, en el orden exacto en que el código lo
espera. Referenciado desde `web/.env.example` y `web/lib/whatsapp.ts`.

> [!IMPORTANT]
> El orden de las variables **ya está codificado** en `templatePayload()`
> (`web/lib/whatsapp.ts:161`). Si cambias la plantilla, cambia esa función en
> el mismo commit o los avisos saldrán con los campos cruzados.

---

## 1. Por qué existe la plantilla

WhatsApp Business solo permite mensajes libres dentro de una **ventana de 24 h**
abierta por un mensaje entrante de esa persona. Un contacto de confianza
**nunca** nos escribió, así que en el caso normal la plantilla no es el
fallback: es el camino principal. Ver [`DECISIONS.md` D11](./DECISIONS.md#d11--whatsapp-la-plantilla-es-el-camino-por-defecto-no-el-fallback).

| Situación | Qué manda el código | Qué recibe el contacto |
|---|---|---|
| Nunca escribió, o pasaron > 24 h | `sendTemplate` con `elb_emergency_alert` | Plantilla + botón "Ver reporte". El mapa va como link dentro del cuerpo. |
| Escribió hace < 24 h (opt-in fresco) | `sendText` con `freeformBody()` + `sendLocation` | Mensaje libre + **tarjeta de ubicación nativa** de WhatsApp. |

---

## 2. Definición a someter

- **Nombre:** `elb_emergency_alert` (debe coincidir con `WHATSAPP_TEMPLATE_NAME`)
- **Categoría:** `UTILITY`
- **Idiomas:** `es` y `en` — hay que someter **las dos**, misma estructura y
  mismo número de variables en cada una.

### Header

`TEXT`, **fijo, sin variables**:

```
🚨 Alerta de emergencia
```

(en inglés: `🚨 Emergency alert`)

Sin variable a propósito: un header con parámetro es una causa habitual de
rechazo y aquí no aporta nada.

### Body — español

> El cuerpo **no puede empezar ni terminar con una variable** (Meta lo
> rechaza). Por eso arranca con "Hola." y cierra con el disclaimer.

```
Hola. {{1}} te registró como contacto de confianza y acaba de activar una alerta de emergencia.

Tipo: {{2}}
Lugar: {{3}}

Este aviso informa, no da instrucciones. No reemplaza a los servicios de emergencia. Si hay peligro, llama al 123.
```

### Body — inglés

```
Hello. {{1}} listed you as a trusted contact and has just triggered an emergency alert.

Type: {{2}}
Place: {{3}}

This message informs, it does not instruct. It does not replace emergency services. If there is danger, call 123, 911 or 112.
```

### Footer

```
Emergency Language Bridge
```

### Botón

Uno solo, `URL` **dinámica**:

- Texto del botón: `Ver reporte` (en inglés: `View report`)
- URL: `https://TU-DOMINIO/r/{{1}}`

La variable del botón es **independiente** de las del cuerpo: vuelve a
numerarse desde `{{1}}` y su valor es el token firmado, no la URL completa.

---

## 3. Contrato de variables (no lo rompas)

| Dónde | Variable | Valor que manda el código | Origen |
|---|---|---|---|
| body | `{{1}}` | nombre de quien llama | `AlertInput.callerName` |
| body | `{{2}}` | tipo de emergencia | `AlertInput.emergencyType` |
| body | `{{3}}` | lugar | `AlertInput.location` |
| button | `{{1}}` | token del link firmado | `link.split("/r/")[1]` |

Si un valor viene vacío el código manda `"-"`: Meta rechaza parámetros vacíos.

El idioma se elige con `locale.startsWith("es") ? "es" : "en"`, así que ambas
versiones deben existir aprobadas o el envío falla para ese idioma.

---

## 4. JSON de creación (Message Templates API)

`POST https://graph.facebook.com/v21.0/{WABA_ID}/message_templates`

```json
{
  "name": "elb_emergency_alert",
  "language": "es",
  "category": "UTILITY",
  "components": [
    {
      "type": "HEADER",
      "format": "TEXT",
      "text": "🚨 Alerta de emergencia"
    },
    {
      "type": "BODY",
      "text": "Hola. {{1}} te registró como contacto de confianza y acaba de activar una alerta de emergencia.\n\nTipo: {{2}}\nLugar: {{3}}\n\nEste aviso informa, no da instrucciones. No reemplaza a los servicios de emergencia. Si hay peligro, llama al 123.",
      "example": {
        "body_text": [["Amara Okafor", "medica", "Carrera 7 #45-12, Bogota"]]
      }
    },
    {
      "type": "FOOTER",
      "text": "Emergency Language Bridge"
    },
    {
      "type": "BUTTONS",
      "buttons": [
        {
          "type": "URL",
          "text": "Ver reporte",
          "url": "https://TU-DOMINIO/r/{{1}}",
          "example": ["https://TU-DOMINIO/r/v1.eyJyZXBvcnRfaWQiOiJlbGJfZGVtbyJ9.QUJD"]
        }
      ]
    }
  ]
}
```

Para `en`: mismo cuerpo con `"language": "en"`, el body en inglés, header
`🚨 Emergency alert` y botón `View report`.

Los bloques `example` son **obligatorios** para revisión: sin ellos Meta
rechaza la plantilla sin revisarla siquiera.

---

## 5. Envío desde el código

`sendTemplate` recibe exactamente esto (`templatePayload()`):

```jsonc
{
  "name": "elb_emergency_alert",
  "language": { "code": "es" },
  "components": [
    { "type": "body", "parameters": [
        { "type": "text", "text": "Amara Okafor" },
        { "type": "text", "text": "medica" },
        { "type": "text", "text": "Carrera 7 #45-12, Bogota" }
    ]},
    { "type": "button", "sub_type": "url", "index": "0",
      "parameters": [{ "type": "text", "text": "v1.eyJ...abc" }] }
  ]
}
```

El `index: "0"` apunta al primer (y único) botón. Si agregas botones, el orden
de `index` es el de la plantilla aprobada.

---

## 6. Flujo de opt-in (es lo que abre la ventana)

```
1. El usuario agrega un contacto de confianza en la app Android (Ajustes).
2. La app le muestra un enlace wa.me hacia NUESTRO número.
   El contacto manda cualquier mensaje ("Hola" sirve).
3. Meta entrega ese mensaje a  POST /api/whatsapp/webhook
   -> escribe  whatsapp_opt_in_at  y  whatsapp_opt_in_ref (id del mensaje)
   -> marca    active = true
   -> responde con una confirmación ("Quedaste registrado como contacto de
      confianza de X. Puedes salir escribiendo BAJA.")
4. Queda abierta la ventana de 24 h -> mientras dure, los avisos salen
   free-form + tarjeta de ubicación nativa. Después, plantilla.
```

Ese único mensaje entrante hace **dos** cosas a la vez: abre la ventana técnica
y deja **consentimiento auditable**. Por eso el opt-in no es un truco de demo.

**Emparejamiento del número:** el webhook busca por los **últimos 10 dígitos**
(`phone_e164 LIKE %suffix`), porque los contactos se guardan en E.164 con `+` y
WhatsApp reporta el número sin `+`.

**Opt-out inmediato, sin bucle de confirmación.** Palabras que lo disparan
(`web/app/api/whatsapp/webhook/route.ts`):

```
baja · stop · salir · unsubscribe · cancelar · no
```

Efecto: `active = false` y `whatsapp_opt_in_at = null`.

---

## 7. Configuración del webhook en Meta

1. Callback URL: `https://TU-DOMINIO/api/whatsapp/webhook`
2. Verify token: el mismo valor que `WHATSAPP_WEBHOOK_VERIFY_TOKEN` en
   `web/.env.local`. El `GET` de la ruta responde el handshake
   (`hub.mode=subscribe` + `hub.challenge`).
3. Suscribirse al campo **`messages`**.

En local, expón el puerto 3000 con un túnel (`ngrok http 3000`) y usa esa URL
pública — Meta no acepta `localhost`.

---

## 8. Variables de entorno relacionadas

```bash
# web/.env.local
KAPSO_API_KEY=                       # ruta recomendada (da storage + historial)
KAPSO_BASE_URL=https://app.kapso.ai/api/meta/
# ...o hablar directo con Meta en vez de Kapso:
META_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_TEMPLATE_NAME=elb_emergency_alert
WHATSAPP_WEBHOOK_VERIFY_TOKEN=
```

`whatsappConfigured()` exige `WHATSAPP_PHONE_NUMBER_ID` **y** una de las dos
credenciales. Si falta algo, el envío devuelve `skipped: true` en vez de
reventar: la llamada nunca se cae por WhatsApp.

---

## 9. Estado real

La plantilla **no ha sido sometida ni aprobada** — no hay cuenta de WhatsApp
Business conectada en este proyecto todavía. El código de envío está escrito y
compila; nunca se mandó un mensaje real. Meta suele tardar de minutos a 24 h en
aprobar una plantilla `UTILITY`, así que **es lo primero que hay que enviar** si
se quiere demostrar el camino de WhatsApp.
