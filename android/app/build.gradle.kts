import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

/**
 * Build-time config comes from android/elb.properties (git-ignored) or from
 * environment variables, so the APK can be pointed at a different backend
 * without editing code. Falls back to localhost for a laptop-only run.
 */
val elbProps = Properties().apply {
    val f = rootProject.file("elb.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

fun cfg(key: String, default: String): String =
    (System.getenv(key) ?: elbProps.getProperty(key) ?: default)

android {
    namespace = "co.elb.app"
    compileSdk = 37
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "co.elb.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0.0"

        // The web app is the only backend the phone talks to: it mints LiveKit
        // tokens and owns the trusted-contact API. No secrets ship in the APK.
        buildConfigField("String", "API_BASE", "\"${cfg("ELB_API_BASE", "http://10.0.2.2:3000")}\"")
        buildConfigField("String", "DEFAULT_ROOM", "\"${cfg("ELB_DEFAULT_ROOM", "elb-demo")}\"")
        buildConfigField("String", "DEMO_USER_ID", "\"${cfg("ELB_DEMO_USER_ID", "11111111-1111-1111-1111-111111111111")}\"")
    }

    signingConfigs {
        // A release keystore if one is supplied, otherwise fall through to the
        // debug key below. Target is a sideloadable APK, not the Play Store.
        create("sideload") {
            val store = cfg("ELB_KEYSTORE", "")
            if (store.isNotBlank() && file(store).exists()) {
                storeFile = file(store)
                storePassword = cfg("ELB_KEYSTORE_PASSWORD", "")
                keyAlias = cfg("ELB_KEY_ALIAS", "elb")
                keyPassword = cfg("ELB_KEY_PASSWORD", "")
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            isMinifyEnabled = false
        }
        release {
            // R8 is off on purpose: WebRTC + LiveKit reflection rules are a
            // classic source of "works in debug, crashes in release", and a
            // sideload demo gains nothing from a smaller APK.
            isMinifyEnabled = false
            isShrinkResources = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("sideload")?.takeIf { it.storeFile != null }
                ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        isCoreLibraryDesugaringEnabled = false
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += setOf(
                "/META-INF/{AL2.0,LGPL2.1}",
                "/META-INF/DEPENDENCIES",
                "/META-INF/INDEX.LIST",
            )
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)
    debugImplementation(libs.androidx.ui.tooling)

    implementation(libs.livekit.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)
    implementation(libs.play.services.location)
}

/**
 * The critical-term glossary is shared with the agent and the operator console.
 * Copying it in at build time means the phone can never highlight a different
 * set of terms than the interpreter actually guarantees.
 */
val syncGlossary by tasks.registering(Copy::class) {
    from(rootProject.file("../shared/critical_terms.json"))
    into(layout.projectDirectory.dir("src/main/assets"))
}
tasks.named("preBuild") { dependsOn(syncGlossary) }
