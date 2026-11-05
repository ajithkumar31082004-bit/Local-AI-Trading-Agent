package com.antigravity.tradingmonitor.data.model

import com.google.gson.annotations.SerializedName

data class HealthResponse(
    val status: String,
    val service: String,
    val timestamp: String,
    val version: String,
    @SerializedName("trading_mode") val tradingMode: String,
    @SerializedName("live_trading_enabled") val liveTradingEnabled: Boolean
)

data class SystemStatus(
    val hostname: String,
    @SerializedName("uptime_seconds") val uptimeSeconds: Double,
    @SerializedName("cpu_percent") val cpuPercent: Double,
    @SerializedName("memory_total_mb") val memoryTotalMb: Double,
    @SerializedName("memory_used_mb") val memoryUsedMb: Double,
    @SerializedName("memory_free_mb") val memoryFreeMb: Double,
    @SerializedName("memory_percent") val memoryPercent: Double,
    @SerializedName("disk_total_gb") val diskTotalGb: Double,
    @SerializedName("disk_used_gb") val diskUsedGb: Double,
    @SerializedName("disk_free_gb") val diskFreeGb: Double,
    @SerializedName("disk_percent") val diskPercent: Double,
    @SerializedName("load_averages") val loadAverages: List<Double>,
    val timestamp: String
)

data class ServiceItem(
    val name: String,
    val active: Boolean,
    val substate: String,
    val pid: Int?,
    val uptime: String?,
    @SerializedName("memory_mb") val memoryMb: Double?,
    val restarts: Int,
    val description: String
)

data class ServicesResponse(
    val services: List<ServiceItem>,
    val timestamp: String
)

data class TradingStatus(
    val symbol: String,
    @SerializedName("trading_mode") val tradingMode: String,
    @SerializedName("live_trading_enabled") val liveTradingEnabled: Boolean,
    @SerializedName("live_trading_acknowledged") val liveTradingAcknowledged: String,
    @SerializedName("ws_connected") val wsConnected: Boolean,
    @SerializedName("runner_ready") val runnerReady: Boolean,
    @SerializedName("current_price") val currentPrice: Double?,
    @SerializedName("market_regime") val marketRegime: String,
    @SerializedName("ticks_ingested") val ticksIngested: Long,
    @SerializedName("completed_5m_bars") val completed5mBars: Int,
    @SerializedName("completed_15m_bars") val completed15mBars: Int,
    @SerializedName("last_candle_timestamp") val lastCandleTimestamp: String?,
    @SerializedName("daily_trade_count") val dailyTradeCount: Int,
    @SerializedName("max_daily_trades") val maxDailyTrades: Int,
    @SerializedName("daily_risk_guard_active") val dailyRiskGuardActive: Boolean,
    @SerializedName("last_updated") val lastUpdated: String
)

data class AccountState(
    val symbol: String,
    @SerializedName("initial_equity") val initialEquity: Double,
    @SerializedName("cash_balance") val cashBalance: Double,
    @SerializedName("current_equity") val currentEquity: Double,
    @SerializedName("realized_pnl") val realizedPnl: Double,
    @SerializedName("unrealized_pnl") val unrealizedPnl: Double,
    @SerializedName("total_fees") val totalFees: Double,
    @SerializedName("total_slippage") val totalSlippage: Double,
    @SerializedName("peak_equity") val peakEquity: Double,
    @SerializedName("open_position") val openPosition: Map<String, Any>?,
    @SerializedName("last_updated") val lastUpdated: String
)

data class GitStatus(
    val branch: String,
    @SerializedName("ec2_commit_sha") val ec2CommitSha: String,
    @SerializedName("remote_commit_sha") val remoteCommitSha: String?,
    @SerializedName("in_sync") val inSync: Boolean,
    @SerializedName("is_clean") val isClean: Boolean,
    @SerializedName("latest_commit_message") val latestCommitMessage: String,
    @SerializedName("latest_commit_author") val latestCommitAuthor: String,
    @SerializedName("latest_commit_date") val latestCommitDate: String,
    @SerializedName("uncommitted_files_count") val uncommittedFilesCount: Int
)

data class GitCommit(
    @SerializedName("commit_sha") val commitSha: String,
    @SerializedName("short_sha") val shortSha: String,
    val author: String,
    val date: String,
    val message: String
)

data class ChangedFile(
    val status: String,
    val file: String
)

data class GitChanges(
    @SerializedName("commit_sha") val commitSha: String,
    @SerializedName("changed_files") val changedFiles: List<ChangedFile>,
    @SerializedName("diff_preview") val diffPreview: String,
    val notice: String
)

data class TradeRecord(
    @SerializedName("trade_id") val tradeId: String,
    @SerializedName("strategy_name") val strategyName: String?,
    val direction: String,
    @SerializedName("entry_time") val entryTime: String,
    @SerializedName("entry_price") val entryPrice: Double,
    val lots: Int,
    @SerializedName("contract_qty") val contractQty: Double,
    @SerializedName("stop_loss") val stopLoss: Double,
    @SerializedName("exit_time") val exitTime: String?,
    @SerializedName("exit_price") val exitPrice: Double?,
    @SerializedName("trigger_price") val triggerPrice: Double?,
    @SerializedName("gross_pnl") val grossPnl: Double,
    @SerializedName("gross_pnl_signal") val grossPnlSignal: Double?,
    @SerializedName("gross_pnl_fill") val grossPnlFill: Double?,
    @SerializedName("brokerage_fee") val brokerageFee: Double?,
    val slippage: Double?,
    @SerializedName("price_impact") val priceImpact: Double?,
    @SerializedName("net_pnl") val netPnl: Double,
    @SerializedName("r_multiple") val rMultiple: Double?,
    @SerializedName("trade_risk_cash") val tradeRiskCash: Double?,
    @SerializedName("exit_reason") val exitReason: String?
)

data class BenchmarkData(
    @SerializedName("total_trades") val totalTrades: Int,
    val notice: String
)

data class TradesResponse(
    @SerializedName("active_forward_test_trades") val activeTrades: List<TradeRecord>,
    @SerializedName("active_trade_count") val activeTradeCount: Int,
    @SerializedName("historical_14d_benchmark") val benchmark: BenchmarkData
)

data class DeploymentRecord(
    val id: String,
    val timestamp: String,
    @SerializedName("commit_sha") val commitSha: String,
    val branch: String,
    val author: String,
    val status: String,
    @SerializedName("tests_passed") val testsPassed: Boolean,
    val message: String
)

data class LogsResponse(
    val service: String,
    @SerializedName("level_filter") val levelFilter: String,
    @SerializedName("total_lines") val totalLines: Int,
    val lines: List<String>,
    val timestamp: String
)

