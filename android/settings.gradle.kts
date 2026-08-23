pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // livekit-android pulls com.github.davidliu:audioswitch from JitPack.
        maven("https://jitpack.io")
    }
}

rootProject.name = "EmergencyLanguageBridge"
include(":app")
