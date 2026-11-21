package com.antigravity.tradingmonitor.data.api

import com.antigravity.tradingmonitor.data.model.*
import retrofit2.http.GET
import retrofit2.http.Query

interface MonitoringApiService {
    @GET("api/health")
    suspend fun getHealth(): HealthResponse

    @GET("api/ec2/status")
    suspend fun getEc2Status(): SystemStatus

    @GET("api/services")
    suspend fun getServices(): ServicesResponse

    @GET("api/trading/status")
    suspend fun getTradingStatus(): TradingStatus

    @GET("api/trading/account")
    suspend fun getAccountState(): AccountState

    @GET("api/trading/trades")
    suspend fun getTrades(): TradesResponse

    @GET("api/git/status")
    suspend fun getGitStatus(): GitStatus

    @GET("api/git/commits")
    suspend fun getGitCommits(@Query("limit") limit: Int = 10): List<GitCommit>

    @GET("api/git/changes")
    suspend fun getGitChanges(): GitChanges

    @GET("api/deployments")
    suspend fun getDeployments(): List<DeploymentRecord>

    @GET("api/logs")
    suspend fun getLogs(
        @Query("service") service: String = "trading",
        @Query("level") level: String = "ALL",
        @Query("limit") limit: Int = 50
    ): LogsResponse
}

