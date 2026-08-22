<img src="./project-logo.png" alt="AuXio" width="160" />

# AuXio

*Nombre interno en el código y en los docs técnicos: **ELB** (Emergency Language Bridge).*

**Intérprete simultáneo para llamadas de emergencia.** El llamante habla en su
idioma desde una app Android, el despachador contesta en español desde una
consola web, y un agente de IA traduce en las dos direcciones en tiempo real —
sin perder las palabras que no se pueden equivocar. Al colgar, la familia
recibe un reporte con solo lo que le corresponde saber.

Platanus Hack 26 · Bogotá · Track 🚨 Emergencies · team-4

---

## Cómo funciona

```
   Android (llamante)  ─┐                            ┌─  Consola web (despachador)
                        │                            │
                        └──►   sala de LiveKit   ◄────┘
                                     ▲
                                     │  dos conexiones, dos tracks nombrados
                             Agente intérprete
                    Deepgram Nova-3 · Claude Haiku 4.5 · ElevenLabs Flash
```

Cada dirección de traducción publica su **propio track de audio**, y cada
cliente se suscribe únicamente al suyo: nadie escucha la interpretación
destinada al otro lado.

## Lo que lo hace distinto

- **Términos que el modelo no puede perder.** 19 términos críticos en 6 idiomas
  (es/en/pt/fr/de/it), en un glosario compartido por el agente, la web y el
  teléfono. Tras cada traducción se verifica que sobrevivieron, y se repara si
  falta un término crítico o si **se invirtió la polaridad** ("is breathing" →
  "no respira"). No fuerza el literal para todo: el ruido entrena a ignorar.
- **Nunca hay silencio.** Si la traducción supera el presupuesto de latencia,
  se habla el original en vez de dejar el turno muerto.
- **Aviso a la familia durante la llamada**, no después: una extracción en
  paralelo dispara la alerta cuando la ubicación y el tipo son estables — y de
  inmediato si aparece *no respira*, *arma*, *incendio* o *atrapado*.
- **Dos reportes distintos, no uno filtrado.** El del familiar se genera por
  separado, así que el detalle clínico, las confianzas y la transcripción
  completa **nunca se escriben** en él. Enlaces firmados con HMAC, con alcance
  propio, expiración y revocación.
- **Informa, no instruye.** No damos indicaciones médicas ni reemplazamos al
  123. La ubicación se lee solo durante una llamada activa: nunca pedimos el
  permiso de ubicación en segundo plano.

## Arrancar

Guía completa en **[`RUNBOOK.md`](RUNBOOK.md)**. Lo mínimo:

```bash
cp agent/.env.example agent/.env        # ELB_REPORT_SIGNING_SECRET y
cp web/.env.example   web/.env.local    # ELB_INTERNAL_TOKEN deben coincidir

cd web     && npm install && npm run dev
cd agent   && py -3.13 -m pip install -r requirements.txt
cd agent   && PYTHONPATH=src py -3.13 -m elb.main dev
cd android && ./gradlew assembleDebug   # APK sideloadable
```

Sin credenciales igual corre lo verificable:

```bash
cd agent && PYTHONPATH=src py -3.13 -m pytest tests/ -q   # 31 passed
cd web   && npx tsc --noEmit && npm run build
```

## Repo

```
shared/critical_terms.json   fuente ÚNICA del glosario (los 3 clientes la copian en build)
agent/                       agente LiveKit en Python: traducción, extracción, reporte, firma
web/                         Next.js: consola del despachador, visor de reportes, API, WhatsApp
android/                     app del llamante (Compose, foreground service microphone|location)
supabase/schema.sql          almacenamiento: contactos, llamadas, reportes, entregas
docs/DECISIONS.md            por qué está hecho así, y los gotchas que ya pagamos
docs/WHATSAPP_TEMPLATE.md    plantilla exacta a someter a Meta + flujo de opt-in
RUNBOOK.md                   levantar todo, generar el APK, demo y troubleshooting
HANDOFF.md                   estado detallado del traspaso
```

## Estado

Verificado ejecutándose: APK real (debug y release), web compilando limpia, 31
tests del agente en verde, e interoperabilidad de firmas Python↔TypeScript
probada — incluida una falsificación de alcance que es rechazada.

**Llamada real de punta a punta, verificada el 2026-08-22.** El llamante habla
inglés, el despachador oye español, y el audio que sale se vuelve a transcribir
para comprobarlo:

```
said  (caller, en): My son is trapped in the back seat and he is not breathing.
                    We are on Seventh Avenue with Forty Fifth street.
heard (operator, es): Mi hijo está atrapado en el asiento trasero y no respira.
                    Estamos en 7th Avenue con 45th Street.
latencia fin-de-habla -> primer audio: 2.33s   ·   cruce de audio: no
```

Reproducible con `py -3.13 scripts/e2e_call.py`. El reporte se generó solo, con
la ubicación y el tipo extraídos, `NOT BREATHING` y `TRAPPED` marcados como
términos críticos y la notificación temprana disparada por fast-path.

Lo que falta: el visor `/r/<token>` necesita Supabase, y WhatsApp necesita la
plantilla aprobada por Meta. Estado sin maquillaje en
[`RUNBOOK.md`](RUNBOOK.md) §8.

## Equipo

- Sergio Alejandro Torres Melendez ([@satorresm12](https://github.com/satorresm12))
- Hugo Guzman ([@hugolenin](https://github.com/hugolenin))
- Santiago Pinzon Cadena ([@santiagopinzon26](https://github.com/santiagopinzon26))
- Julian Camilo Rivera Guzmán ([@jcriverag1](https://github.com/jcriverag1))
- Andres Bolivar ([@andresbolivargit](https://github.com/andresbolivargit))

> Nota de deploy: Vercel/Render solo pueden conectarse a repos propios, no a
> este repo de la organización. Para desplegar, espejen el repo a una cuenta
> personal y agreguen ese remoto como segundo destino de `git push`.
