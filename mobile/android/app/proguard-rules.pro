# ProGuard rules for AI Trading Agent Android Monitor
-keepattributes *Annotation*,Signature,InnerClasses,EnclosingMethod

# Kotlin reflection and metadata
-keepclassmembers class * {
    @androidx.annotation.Keep *;
}

# Preserve serialized JSON data classes
-keep class com.antigravity.tradingmonitor.data.model.** { *; }
-keepclassmembers class com.antigravity.tradingmonitor.data.model.** { *; }
-keepclassmembers class * {
    @com.google.gson.annotations.SerializedName <fields>;
}

# Preserve Retrofit API interfaces
-keep interface com.antigravity.tradingmonitor.data.api.** { *; }
-keepclassmembers,allowobfuscation interface * {
    @retrofit2.http.* <methods>;
}

# Android Jetpack Compose
-keep class androidx.compose.** { *; }

# Suppress benign warnings for compile-time Error Prone annotations referenced by Google Crypto Tink
-dontwarn com.google.errorprone.annotations.**

