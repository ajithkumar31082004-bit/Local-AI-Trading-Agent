package com.antigravity.tradingmonitor.data.api

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {
    private const val PREFS_NAME = "trading_monitor_secure_prefs"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_API_TOKEN = "api_token"

    const val DEFAULT_BASE_URL = "http://16.4.36.144:8088/"
    const val DEFAULT_API_TOKEN = "delta_paper_monitor_sec_key_2026_xaut"

    private var currentRetrofit: Retrofit? = null
    private var currentBaseUrl: String? = null
    private var currentApiToken: String? = null

    private fun getSecurePrefs(context: Context): SharedPreferences {
        return try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()

            EncryptedSharedPreferences.create(
                context,
                PREFS_NAME,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
        } catch (e: Exception) {
            context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        }
    }

    fun getBaseUrl(context: Context): String {
        val prefs = getSecurePrefs(context)
        var url = prefs.getString(KEY_BASE_URL, DEFAULT_BASE_URL) ?: DEFAULT_BASE_URL
        if (!url.endsWith("/")) {
            url += "/"
        }
        return url
    }

    fun setBaseUrl(context: Context, url: String) {
        var cleanUrl = url.trim()
        if (!cleanUrl.endsWith("/")) {
            cleanUrl += "/"
        }
        getSecurePrefs(context).edit().putString(KEY_BASE_URL, cleanUrl).apply()
        currentRetrofit = null
    }

    fun getApiToken(context: Context): String {
        return getSecurePrefs(context).getString(KEY_API_TOKEN, DEFAULT_API_TOKEN) ?: DEFAULT_API_TOKEN
    }

    fun setApiToken(context: Context, token: String) {
        getSecurePrefs(context).edit().putString(KEY_API_TOKEN, token.trim()).apply()
        currentRetrofit = null
    }

    fun getService(context: Context): MonitoringApiService {
        val baseUrl = getBaseUrl(context)
        val token = getApiToken(context)

        if (currentRetrofit != null && currentBaseUrl == baseUrl && currentApiToken == token) {
            return currentRetrofit!!.create(MonitoringApiService::class.java)
        }

        currentBaseUrl = baseUrl
        currentApiToken = token

        val authInterceptor = Interceptor { chain ->
            val original = chain.request()
            val requestBuilder = original.newBuilder()
                .header("Authorization", "Bearer $token")
                .header("Accept", "application/json")
                .header("User-Agent", "AITradingAgentMonitor/1.0.0 (Android)")
            chain.proceed(requestBuilder.build())
        }

        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        }

        val okHttpClient = OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .addInterceptor(logging)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.SECONDS)
            .build()

        currentRetrofit = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()

        return currentRetrofit!!.create(MonitoringApiService::class.java)
    }
}

