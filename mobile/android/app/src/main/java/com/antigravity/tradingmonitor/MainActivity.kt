package com.antigravity.tradingmonitor

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.antigravity.tradingmonitor.data.repository.MonitoringRepository
import com.antigravity.tradingmonitor.theme.*
import com.antigravity.tradingmonitor.ui.screens.*

sealed class Screen(val route: String, val title: String, val icon: ImageVector) {
    object Overview : Screen("overview", "Overview", Icons.Default.Dashboard)
    object Code : Screen("code", "Code", Icons.Default.Code)
    object Services : Screen("services", "Services", Icons.Default.Storage)
    object Trades : Screen("trades", "Trades", Icons.Default.History)
    object Logs : Screen("logs", "Logs", Icons.Default.Terminal)
}

@OptIn(ExperimentalMaterial3Api::class)
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val repository = MonitoringRepository(applicationContext)

        setContent {
            AITradingMonitorTheme {
                var currentScreen by remember { mutableStateOf<Screen>(Screen.Overview) }
                var showSettings by remember { mutableStateOf(false) }

                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = {
                                Text(
                                    text = "AI Trading Agent",
                                    color = TextPrimary,
                                    fontSize = 18.sp,
                                    fontWeight = FontWeight.Bold
                                )
                            },
                            actions = {
                                IconButton(onClick = { showSettings = true }) {
                                    Icon(
                                        imageVector = Icons.Default.Settings,
                                        contentDescription = "Settings",
                                        tint = GoldPrimary
                                    )
                                }
                            },
                            colors = TopAppBarDefaults.topAppBarColors(containerColor = BackgroundDark)
                        )
                    },
                    bottomBar = {
                        NavigationBar(
                            containerColor = SurfaceDark,
                            tonalElevation = 4.dp
                        ) {
                            val items = listOf(
                                Screen.Overview,
                                Screen.Code,
                                Screen.Services,
                                Screen.Trades,
                                Screen.Logs
                            )
                            items.forEach { screen ->
                                val selected = currentScreen.route == screen.route
                                NavigationBarItem(
                                    icon = {
                                        Icon(
                                            imageVector = screen.icon,
                                            contentDescription = screen.title
                                        )
                                    },
                                    label = { Text(screen.title, fontSize = 10.sp) },
                                    selected = selected,
                                    onClick = { currentScreen = screen },
                                    colors = NavigationBarItemDefaults.colors(
                                        selectedIconColor = BackgroundDark,
                                        selectedTextColor = GoldPrimary,
                                        indicatorColor = GoldPrimary,
                                        unselectedIconColor = TextSecondary,
                                        unselectedTextColor = TextSecondary
                                    )
                                )
                            }
                        }
                    },
                    containerColor = BackgroundDark
                ) { innerPadding ->
                    val modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding)

                    when (currentScreen) {
                        Screen.Overview -> OverviewScreen(repository, modifier)
                        Screen.Code -> CodeChangesScreen(repository, modifier)
                        Screen.Services -> ServicesScreen(repository, modifier)
                        Screen.Trades -> TradeHistoryScreen(repository, modifier)
                        Screen.Logs -> LogsScreen(repository, modifier)
                    }

                    if (showSettings) {
                        SettingsDialog(
                            context = applicationContext,
                            onDismiss = { showSettings = false },
                            onSaved = { /* trigger refresh if needed */ }
                        )
                    }
                }
            }
        }
    }
}
