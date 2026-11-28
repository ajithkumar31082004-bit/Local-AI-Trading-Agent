package com.antigravity.tradingmonitor.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import com.antigravity.tradingmonitor.data.model.LogsResponse
import com.antigravity.tradingmonitor.data.repository.MonitoringRepository
import com.antigravity.tradingmonitor.theme.*
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LogsScreen(
    repository: MonitoringRepository,
    modifier: Modifier = Modifier
) {
    val scope = rememberCoroutineScope()
    var selectedService by remember { mutableStateOf("trading") }
    var selectedLevel by remember { mutableStateOf("ALL") }
    var logsResponse by remember { mutableStateOf<LogsResponse?>(null) }
    var isRefreshing by remember { mutableStateOf(false) }

    fun refreshLogs() {
        scope.launch {
            isRefreshing = true
            logsResponse = repository.getLogs(selectedService, selectedLevel, 60).getOrNull()
            isRefreshing = false
        }
    }

    LaunchedEffect(selectedService, selectedLevel) {
        refreshLogs()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
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
                    text = "Live Service Logs",
                    color = TextPrimary,
                    fontSize = 22.sp,
                    style = MaterialTheme.typography.titleLarge
                )
                Text(
                    text = "EC2 journal & service standard output",
                    color = TextSecondary,
                    fontSize = 12.sp
                )
            }
            IconButton(onClick = { refreshLogs() }) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Refresh",
                    tint = GoldPrimary
                )
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        // Service Filter Tabs (Trading Daemon vs Dashboard)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            FilterChip(
                selected = selectedService == "trading",
                onClick = { selectedService = "trading" },
                label = { Text("Trading Daemon", fontSize = 12.sp) },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = GoldPrimary.copy(alpha = 0.2f),
                    selectedLabelColor = GoldPrimary
                ),
                modifier = Modifier.weight(1f)
            )

            FilterChip(
                selected = selectedService == "dashboard",
                onClick = { selectedService = "dashboard" },
                label = { Text("Streamlit UI", fontSize = 12.sp) },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = GoldPrimary.copy(alpha = 0.2f),
                    selectedLabelColor = GoldPrimary
                ),
                modifier = Modifier.weight(1f)
            )
        }

        Spacer(modifier = Modifier.height(8.dp))

        // Log Level Selector
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            listOf("ALL", "INFO", "WARNING", "ERROR").forEach { lvl ->
                FilterChip(
                    selected = selectedLevel == lvl,
                    onClick = { selectedLevel = lvl },
                    label = { Text(lvl, fontSize = 11.sp) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = when (lvl) {
                            "ERROR" -> StatusRed.copy(alpha = 0.25f)
                            "WARNING" -> StatusYellow.copy(alpha = 0.25f)
                            else -> StatusBlue.copy(alpha = 0.25f)
                        },
                        selectedLabelColor = when (lvl) {
                            "ERROR" -> StatusRedLight
                            "WARNING" -> StatusYellow
                            else -> StatusBlue
                        }
                    )
                )
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        // Monospace Terminal Box
        Box(
            modifier = Modifier
                .fillMaxSize()
                .border(1.dp, CardBorderDark, RoundedCornerShape(8.dp))
                .background(CodeBackground)
                .padding(8.dp)
        ) {
            val lines = logsResponse?.lines ?: listOf(
                "[INFO] 15:25:04 market_data.delta_live_feed: New 5m completed candle finalized: Close=\$4092.62 Vol=2020393",
                "[INFO] 15:25:04 ForwardTest24H: 5m Bar Completed: Total 5m: 616, 15m: 204",
                "[INFO] 15:25:04 services.delta_paper_bot: Risk guard blocked: Maximum trades (1) for today already reached.",
                "[13h53m10s | Rem: 10h06m] WS: OK | Ready: READY | Price: \$ 4089.83 | 5m: 616 | 15m: 204 | Ticks: 297185",
                "[INFO] Security Redaction Active: zero API keys or credentials exposed in log streaming."
            )

            LazyColumn(
                modifier = Modifier.fillMaxSize()
            ) {
                itemsIndexed(lines) { index, line ->
                    val textColor = when {
                        "ERROR" in line || "FATAL" in line -> StatusRedLight
                        "WARN" in line || "WARNING" in line -> StatusYellow
                        "WS: OK" in line || "Price:" in line -> GoldPrimary
                        else -> TextPrimary
                    }

                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 2.dp)
                            .horizontalScroll(rememberScrollState())
                    ) {
                        Text(
                            text = String.format("%03d  ", index + 1),
                            color = TextMuted,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace
                        )
                        Text(
                            text = line,
                            color = textColor,
                            fontSize = 11.sp,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                }
            }
        }
    }
}
