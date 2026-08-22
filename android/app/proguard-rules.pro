# R8 is disabled for the sideload build (see app/build.gradle.kts). These rules
# exist so enabling it later is a one-line change rather than a debugging session.
-keep class io.livekit.** { *; }
-keep class livekit.** { *; }
-keep class org.webrtc.** { *; }
-keepclassmembers class ** { @kotlinx.serialization.SerialName <fields>; }
-keepattributes *Annotation*, InnerClasses, Signature
