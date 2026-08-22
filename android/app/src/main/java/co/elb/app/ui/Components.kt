package co.elb.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import co.elb.app.data.Glossary
import co.elb.app.ui.theme.Crit
import co.elb.app.ui.theme.CritContainer
import co.elb.app.ui.theme.Info
import co.elb.app.ui.theme.Ok
import co.elb.app.ui.theme.Warn
import co.elb.app.ui.theme.WarnContainer

/**
 * Text with critical glossary terms highlighted, driven by the shared
 * glossary. Underline as well as colour, so the signal survives
 * colour-blindness and a cracked screen in bad light.
 */
@Composable
fun GlossaryText(
    text: String,
    lang: String,
    glossary: Glossary,
    modifier: Modifier = Modifier,
    style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.bodyLarge,
    baseColor: Color = MaterialTheme.colorScheme.onSurface,
) {
    val annotated = remember(text, lang) {
        buildAnnotatedString {
            for (seg in glossary.highlight(text, lang)) {
                when (seg.severity) {
                    "critical" -> withStyle(
                        SpanStyle(
                            color = Color(0xFFFECACA),
                            background = CritContainer,
                            fontWeight = FontWeight.Bold,
                            textDecoration = TextDecoration.Underline,
                        ),
                    ) { append(seg.text) }

                    "high" -> withStyle(
                        SpanStyle(
                            color = Color(0xFFFDE68A),
                            background = WarnContainer,
                            fontWeight = FontWeight.SemiBold,
                        ),
                    ) { append(seg.text) }

                    "info" -> withStyle(
                        SpanStyle(color = Color(0xFFBAE6FD), background = Color(0xFF0C4A6E)),
                    ) { append(seg.text) }

                    else -> withStyle(SpanStyle(color = baseColor)) { append(seg.text) }
                }
            }
        }
    }
    Text(annotated, modifier = modifier, style = style)
}

@Composable
fun StatePill(label: String, state: String, modifier: Modifier = Modifier) {
    val (color, text) = when (state) {
        "processing" -> Warn to "traduciendo"
        "speaking" -> Ok to "hablando"
        "fallback" -> Crit to "respaldo"
        "error" -> Crit to "error"
        else -> MaterialTheme.colorScheme.outline to "escuchando"
    }
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(RoundedCornerShape(4.dp)).background(color))
        Text(
            "  $label · $text",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * The product's ethical guarantee. Rendered as a component so no screen can
 * quietly ship without it.
 */
@Composable
fun EthicsNotice(modifier: Modifier = Modifier, compact: Boolean = false) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f))
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            "Esta app informa, no da instrucciones.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurface,
        )
        if (!compact) {
            Text(
                "Nunca genera ni sugiere procedimientos médicos o de rescate. " +
                    "No reemplaza a los servicios de emergencia profesionales.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(
            "Si hay peligro inmediato, llama al 123 / 911 / 112.",
            style = MaterialTheme.typography.bodyMedium,
            color = Info,
        )
    }
}
