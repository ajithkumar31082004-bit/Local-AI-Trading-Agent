package com.antigravity.tradingmonitor.data.repository

import android.content.Context
import com.antigravity.tradingmonitor.data.api.ApiClient
import com.antigravity.tradingmonitor.data.model.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class MonitoringRepository(private val context: Context) {

    private val api get() = ApiClient.getService(context)

    suspend fun checkHealth(): Result<HealthResponse> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getHealth())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getSystemStatus(): Result<SystemStatus> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getEc2Status())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getServices(): Result<ServicesResponse> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getServices())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getTradingStatus(): Result<TradingStatus> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getTradingStatus())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getAccountState(): Result<AccountState> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getAccountState())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getTrades(): Result<TradesResponse> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getTrades())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getGitStatus(): Result<GitStatus> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getGitStatus())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getGitCommits(limit: Int = 10): Result<List<GitCommit>> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getGitCommits(limit))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getGitChanges(): Result<GitChanges> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getGitChanges())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getDeployments(): Result<List<DeploymentRecord>> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getDeployments())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun getLogs(service: String = "trading", level: String = "ALL", limit: Int = 50): Result<LogsResponse> = withContext(Dispatchers.IO) {
        try {
            Result.success(api.getLogs(service, level, limit))
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}

