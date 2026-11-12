# ProGuard rules for AI Trading Agent Android Monitor
-keepattributes *Annotation*,Signature,InnerClasses,EnclosingMethod

# Kotlin reflection and metadata
-keepclassmembers class * {
    @androidx.annotation.Keep *;
}

# Preserve serialized JSON data classes
-keepclassmembers class * {
    @com.google.gson.annotations.SerializedName <fields>;
}

# Android Jetpack Compose
-keep class androidx.compose.** { *; }

# Suppress benign warnings for compile-time Error Prone annotations referenced by Google Crypto Tink
-dontwarn com.google.errorprone.annotations.**

