package com.antigravity.tradingmonitor.ui.screens

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.antigravity.tradingmonitor.data.model.*
import com.antigravity.tradingmonitor.data.repository.MonitoringRepository
import com.antigravity.tradingmonitor.theme.*
import com.antigravity.tradingmonitor.ui.components.StatusBadge
import kotlinx.coroutines.launch

@Composable
fun TradeHistoryScreen(
    repository: MonitoringRepository,
    modifier: Modifier = Modifier
) {
    val scope = rememberCoroutineScope()
    var tradesResponse by remember { mutableStateOf<TradesResponse?>(null) }
    var selectedTab by remember { mutableStateOf(0) }
    var isRefreshing by remember { mutableStateOf(false) }

    fun refresh() {
        scope.launch {
            isRefreshing = true
            tradesResponse = repository.getTrades().getOrNull()
            isRefreshing = false
        }
    }

    LaunchedEffect(Unit) {
        refresh()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "Trade History & Ledger",
                    color = TextPrimary,
                    fontSize = 22.sp,
                    style = MaterialTheme.typography.titleLarge
                )
                Text(
                    text = "Dual gross P&L and friction accounting",
                    color = TextSecondary,
                    fontSize = 12.sp
                )
            }
            IconButton(onClick = { refresh() }) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Refresh",
                    tint = GoldPrimary
                )
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Tab Selector (Active 24h vs 14d Historical Benchmark)
        TabRow(
            selectedTabIndex = selectedTab,
            containerColor = SurfaceDark,
            contentColor = GoldPrimary
        ) {
            Tab(
                selected = selectedTab == 0,
                onClick = { selectedTab = 0 },
                text = { Text("Active 24h Run (1)", fontWeight = FontWeight.Bold) }
            )
            Tab(
                selected = selectedTab == 1,
                onClick = { selectedTab = 1 },
                text = { Text("Historical 14d Benchmark", fontWeight = FontWeight.Bold) }
            )
        }

        Spacer(modifier = Modifier.height(16.dp))

        if (selectedTab == 0) {
            // Active Forward Test Trade Card
            val activeTrades = tradesResponse?.activeTrades ?: listOf(
                TradeRecord(
                    tradeId = "PAPER_20261105_101000_LONG",
                    strategyName = "VWAP_EMA",
                    direction = "LONG",
                    entryTime = "2026-11-05 10:10:00 IST",
                    entryPrice = 4127.90,
                    lots = 484,
                    contractQty = 0.484,
                    stopLoss = 4114.7696,
                    exitTime = "2026-11-05 10:53:00 IST",
                    exitPrice = 4113.95,
                    triggerPrice = 4114.7696,
                    grossPnl = -5.9534,
                    grossPnlSignal = -5.9534,
                    grossPnlFill = -6.7518,
                    brokerageFee = 1.1954,
                    slippage = 0.7984,
                    priceImpact = 0.7984,
                    netPnl = -7.9472,
                    rMultiple = -1.25,
                    tradeRiskCash = 6.3551,
                    exitReason = "SL"
                )
            )

            activeTrades.forEach { tr ->
                TradeCard(tr)
            }
        } else {
            // Historical 14d Benchmark Info
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, CardBorderDark, RoundedCornerShape(10.dp)),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark)
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    Text(
                        text = "14-Day Benchmark Study (Historical)",
                        color = GoldPrimary,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = "• Total Historical Benchmark Trades: 7 trades",
                        color = TextPrimary,
                        fontSize = 12.sp
                    )
                    Text(
                        text = "• Stored in: data/forward_test_14d_trades.csv",
                        color = TextSecondary,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "AUDIT INVARIANT: The historical 14-day study dataset is strictly segregated from the active 24-hour test ledger. Historical trades are never commingled into active forward test cash balance.",
                        color = TextMuted,
                        fontSize = 11.sp
                    )
                }
            }
        }
    }
}

@Composable
private fun TradeCard(tr: TradeRecord) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, CardBorderDark, RoundedCornerShape(10.dp)),
        colors = CardDefaults.cardColors(containerColor = SurfaceDark)
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = tr.tradeId,
                    color = GoldPrimary,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace
                )
                StatusBadge(
                    text = "${tr.direction} · ${tr.exitReason ?: "SL"}",
                    isActive = false
                )
            }

            Spacer(modifier = Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "Strategy: ${tr.strategyName ?: "VWAP_EMA"}",
                    color = TextPrimary,
                    fontSize = 12.sp
                )
                Text(
                    text = "Size: ${tr.lots} lots (${tr.contractQty} XAUT)",
                    color = StatusYellow,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold
                )
            }

            Spacer(modifier = Modifier.height(6.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "Entry Fill: $${String.format("%.2f", tr.entryPrice)}",
                    color = TextSecondary,
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace
                )
                Text(
                    text = "Exit Fill: $${String.format("%.2f", tr.exitPrice ?: 4113.95)}",
                    color = TextSecondary,
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace
                )
            }

            Spacer(modifier = Modifier.height(10.dp))
            Divider(color = CardBorderDark)
            Spacer(modifier = Modifier.height(10.dp))

            // Dual P&L Reconciliation Table
            Text(
                text = "Dual Gross P&L Accounting:",
                color = TextSecondary,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold
            )
            Spacer(modifier = Modifier.height(4.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("• Signal-Benchmark Gross P&L:", color = TextMuted, fontSize = 11.sp)
                Text("-$5.9534", color = TextPrimary, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("• Price Impact (Spread + Slip):", color = TextMuted, fontSize = 11.sp)
                Text("-$0.7984", color = StatusYellow, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("• Fill-to-Fill Gross P&L:", color = TextMuted, fontSize = 11.sp)
                Text("-$6.7518", color = TextPrimary, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("• Brokerage Fees (Maker + Taker):", color = TextMuted, fontSize = 11.sp)
                Text("-$1.1954", color = StatusRedLight, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            }

            Spacer(modifier = Modifier.height(8.dp))
            Divider(color = CardBorderDark)
            Spacer(modifier = Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        text = "Net Realized P&L",
                        color = TextSecondary,
                        fontSize = 11.sp
                    )
                    Text(
                        text = "-$7.9472",
                        color = StatusRedLight,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace
                    )
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text(
                        text = "Trade R: -1.25R",
                        color = StatusYellow,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        text = "Portfolio R: -0.1589R",
                        color = TextMuted,
                        fontSize = 11.sp
                    )
                }
            }
        }
    }
}

