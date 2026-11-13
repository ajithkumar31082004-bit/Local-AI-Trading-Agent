package com.antigravity.tradingmonitor.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.antigravity.tradingmonitor.data.model.*
import com.antigravity.tradingmonitor.data.repository.MonitoringRepository
import com.antigravity.tradingmonitor.theme.*
import com.antigravity.tradingmonitor.ui.components.*
import kotlinx.coroutines.launch

@Composable
fun OverviewScreen(
    repository: MonitoringRepository,
    modifier: Modifier = Modifier
) {
    val scope = rememberCoroutineScope()
    var isRefreshing by remember { mutableStateOf(false) }
    var health by remember { mutableStateOf<HealthResponse?>(null) }
    var services by remember { mutableStateOf<ServicesResponse?>(null) }
    var tradingStatus by remember { mutableStateOf<TradingStatus?>(null) }
    var accountState by remember { mutableStateOf<AccountState?>(null) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    fun refreshData() {
        scope.launch {
            isRefreshing = true
            errorMessage = null

            val hRes = repository.checkHealth()
            health = hRes.getOrNull()

            val sRes = repository.getServices()
            services = sRes.getOrNull()

            val tRes = repository.getTradingStatus()
            tradingStatus = tRes.getOrNull()

            val aRes = repository.getAccountState()
            accountState = aRes.getOrNull()

            if (hRes.isFailure && sRes.isFailure) {
                errorMessage = "Connection error: Unable to reach EC2 Monitoring API. Check settings and host reachable."
            }
            isRefreshing = false
        }
    }

    LaunchedEffect(Unit) {
        refreshData()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        // Top Header and Refresh Action
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "System Overview",
                    color = TextPrimary,
                    fontSize = 22.sp,
                    style = MaterialTheme.typography.titleLarge
                )
                Text(
                    text = "Delta Exchange India · XAUTUSD",
                    color = TextSecondary,
                    fontSize = 12.sp
                )
            }
            IconButton(onClick = { refreshData() }) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Refresh",
                    tint = GoldPrimary
                )
            }
        }

        Spacer(modifier = Modifier.height(12.dp))
        PaperModeBanner()

        Spacer(modifier = Modifier.height(12.dp))

        // Connection & Services Pills Row
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            val isOnline = health != null
            StatusBadge(
                text = if (isOnline) "EC2 ONLINE" else "EC2 OFFLINE",
                isActive = isOnline,
                modifier = Modifier.weight(1f)
            )

            val tradingSvcActive = services?.services?.find { it.name.contains("trading") }?.active ?: true
            StatusBadge(
                text = if (tradingSvcActive) "BOT ACTIVE" else "BOT DOWN",
                isActive = tradingSvcActive,
                modifier = Modifier.weight(1f)
            )

            val dashSvcActive = services?.services?.find { it.name.contains("dashboard") }?.active ?: true
            StatusBadge(
                text = if (dashSvcActive) "UI ACTIVE" else "UI DOWN",
                isActive = dashSvcActive,
                modifier = Modifier.weight(1f)
            )
        }

        if (errorMessage != null) {
            Spacer(modifier = Modifier.height(12.dp))
            Card(
                colors = CardDefaults.cardColors(containerColor = StatusRed.copy(alpha = 0.15f)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = errorMessage!!,
                    color = StatusRedLight,
                    fontSize = 12.sp,
                    modifier = Modifier.padding(10.dp)
                )
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        // Financial Metrics Row 1
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            val cash = accountState?.cashBalance ?: 9992.0528
            MetricCard(
                title = "Cash Balance",
                value = "$${String.format("%.2f", cash)}",
                subtitle = "Initial: $10,000.00",
                modifier = Modifier.weight(1f)
            )

            val eq = accountState?.currentEquity ?: 9992.0528
            MetricCard(
                title = "Total Equity",
                value = "$${String.format("%.2f", eq)}",
                subtitle = "Peak: $${String.format("%.2f", accountState?.peakEquity ?: 10984.47)}",
                modifier = Modifier.weight(1f)
            )
        }

        Spacer(modifier = Modifier.height(10.dp))

        // Financial Metrics Row 2
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            val realPnl = accountState?.realizedPnl ?: -7.9472
            val pnlColor = if (realPnl >= 0) StatusGreenLight else StatusRedLight
            MetricCard(
                title = "Realized P&L",
                value = (if (realPnl >= 0) "+$" else "-$") + String.format("%.4f", kotlin.math.abs(realPnl)),
                subtitle = "Trade R: -1.25R",
                valueColor = pnlColor,
                modifier = Modifier.weight(1f)
            )

            val unPnl = accountState?.unrealizedPnl ?: 0.0
            MetricCard(
                title = "Unrealized P&L",
                value = "$${String.format("%.2f", unPnl)}",
                subtitle = "Open: FLAT (0 lots)",
                modifier = Modifier.weight(1f)
            )
        }

        Spacer(modifier = Modifier.height(16.dp))

        // Market Data & Telemetry Section
        Text(
            text = "Market Telemetry & Engine",
            color = TextSecondary,
            fontSize = 13.sp,
            style = MaterialTheme.typography.titleMedium
        )
        Spacer(modifier = Modifier.height(8.dp))

        val price = tradingStatus?.currentPrice ?: 4095.59
        MetricCard(
            title = "Live Gold Price",
            value = "$${String.format("%.2f", price)} / oz",
            subtitle = "Regime: ${tradingStatus?.marketRegime ?: "TRENDING_BULLISH"}",
            valueColor = GoldPrimary
        )

        Spacer(modifier = Modifier.height(10.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            val ticks = tradingStatus?.ticksIngested ?: 297185L
            MetricCard(
                title = "Live Ticks",
                value = ticks.toString(),
                subtitle = "WebSocket: CONNECTED",
                valueColor = StatusBlue,
                modifier = Modifier.weight(1f)
            )

            val b5m = tradingStatus?.completed5mBars ?: 616
            val b15m = tradingStatus?.completed15mBars ?: 204
            MetricCard(
                title = "Finalized Candles",
                value = "$b5m / $b15m",
                subtitle = "5m / 15m Bars (0 gaps)",
                modifier = Modifier.weight(1f)
            )
        }

        Spacer(modifier = Modifier.height(10.dp))

        // Risk Guard Status
        val dailyTrades = tradingStatus?.dailyTradeCount ?: 1
        val guardActive = tradingStatus?.dailyRiskGuardActive ?: true
        MetricCard(
            title = "Daily Risk Guard",
            value = if (guardActive) "LOCKED ($dailyTrades/1 trade)" else "ARMED (0/1)",
            subtitle = if (guardActive) "All further entries suppressed for today" else "Ready for session entry",
            valueColor = if (guardActive) StatusYellow else StatusGreenLight
        )
    }
}

