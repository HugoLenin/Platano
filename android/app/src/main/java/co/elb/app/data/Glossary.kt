package co.elb.app.data

import android.content.Context
import kotlinx.serialization.Serializable
import java.text.Normalizer

/**
 * Caller-side highlighting from the SAME shared glossary the agent enforces
 * and the operator console renders.
 *
 * assets/critical_terms.json is copied in at build time by the `syncGlossary`
 * Gradle task from shared/critical_terms.json. There is no second list.
 */

@Serializable
data class GlossaryTerm(
    val id: String,
    val severity: String,
    val polarity: String,
    val pair: String? = null,
    val fixed: Map<String, String> = emptyMap(),
    val match: Map<String, List<String>> = emptyMap(),
)

@Serializable
data class GlossaryFile(
    val version: String = "0",
    val languages: List<String> = emptyList(),
    val terms: List<GlossaryTerm> = emptyList(),
)

data class Segment(val text: String, val termId: String? = null, val severity: String? = null)

class Glossary private constructor(private val file: GlossaryFile) {

    val version: String get() = file.version
    private val byId = file.terms.associateBy { it.id }
    private val compiled = mutableMapOf<String, List<Pair<String, Regex>>>()

    fun fixedFor(termId: String, lang: String): String =
        byId[termId]?.fixed?.get(lang) ?: termId.replace('_', ' ').uppercase()

    fun severityOf(termId: String): String = byId[termId]?.severity ?: "info"

    private fun patterns(lang: String): List<Pair<String, Regex>> = compiled.getOrPut(lang) {
        file.terms.flatMap { term ->
            (term.match[lang] ?: emptyList()).mapNotNull { phrase ->
                val p = normalize(phrase)
                if (p.isEmpty()) null
                else term.id to Regex("(?<!\\p{L})${Regex.escape(p)}(?!\\p{L})")
            }
        }.sortedByDescending { it.second.pattern.length } // longest wins: negations survive
    }

    /**
     * Split text into plain and highlighted segments.
     *
     * Matching runs on a normalised copy, but spans are mapped back to the
     * ORIGINAL string so the caller sees their own words untouched.
     */
    fun highlight(text: String, lang: String): List<Segment> {
        if (text.isEmpty() || lang !in file.languages) return listOf(Segment(text))

        val sb = StringBuilder()
        val indexMap = ArrayList<Int>(text.length)
        var lastWasSpace = false
        for (i in text.indices) {
            val piece = normalize(text[i].toString())
            if (piece.isEmpty()) continue
            for (ch in piece) {
                val isSpace = ch == ' '
                if (isSpace && lastWasSpace) continue
                sb.append(ch)
                indexMap.add(i)
                lastWasSpace = isSpace
            }
        }
        val norm = sb.toString()
        if (norm.isEmpty()) return listOf(Segment(text))

        val claimed = ArrayList<Triple<Int, Int, String>>()
        for ((termId, re) in patterns(lang)) {
            for (m in re.findAll(norm)) {
                val s = m.range.first
                val e = m.range.last + 1
                if (claimed.any { s < it.second && it.first < e }) continue
                claimed.add(Triple(s, e, termId))
            }
        }
        if (claimed.isEmpty()) return listOf(Segment(text))
        claimed.sortBy { it.first }

        val out = ArrayList<Segment>()
        var cursor = 0
        for ((s, e, termId) in claimed) {
            val origStart = indexMap.getOrElse(s) { 0 }
            val origEnd = indexMap.getOrElse(e - 1) { text.length - 1 } + 1
            if (origStart > cursor) out.add(Segment(text.substring(cursor, origStart)))
            out.add(Segment(text.substring(origStart, origEnd), termId, severityOf(termId)))
            cursor = origEnd
        }
        if (cursor < text.length) out.add(Segment(text.substring(cursor)))
        return out
    }

    companion object {
        @Volatile
        private var instance: Glossary? = null

        /** Same normalisation as glossary.py / glossary.ts. */
        fun normalize(input: String): String {
            val apostrophes = input.replace('’', '\'').replace('ʼ', '\'')
            val decomposed = Normalizer.normalize(apostrophes, Normalizer.Form.NFD)
            val stripped = decomposed.filterNot { Character.getType(it) == Character.NON_SPACING_MARK.toInt() }
            return stripped
                .lowercase()
                .map { if (it.isLetterOrDigit() || it == '\'' || it == '-' || it.isWhitespace()) it else ' ' }
                .joinToString("")
                .replace(Regex("\\s+"), " ")
                .trim()
        }

        fun load(context: Context): Glossary = instance ?: synchronized(this) {
            instance ?: run {
                val json = runCatching {
                    context.assets.open("critical_terms.json").bufferedReader().use { it.readText() }
                }.getOrNull()
                val parsed = json
                    ?.let { runCatching { ElbJson.decodeFromString(GlossaryFile.serializer(), it) }.getOrNull() }
                    ?: GlossaryFile()
                Glossary(parsed).also { instance = it }
            }
        }
    }
}
