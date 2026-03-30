/**
 * Autonomous Trading Dashboard - JavaScript
 * Real-time data updates and interactivity
 */

// Global state
let dashboardData = null;
let charts = {
    quality: null,
    allocation: null,
    performance: null,
    winRate: null,
    regime: null,
    tradeResults: null
};
let updateInterval = null;

// Initialize dashboard on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('Dashboard initializing...');
    initializeDashboard();
    updateClock();
    setInterval(updateClock, 1000);
    setInterval(refreshData, 5000); // Refresh every 5 seconds
});

/**
 * Initialize dashboard
 */
function initializeDashboard() {
    refreshData();
    initializeCharts();
}

/**
 * Refresh all dashboard data from API
 */
function refreshData() {
    fetch('/api/data')
        .then(response => response.json())
        .then(data => {
            dashboardData = data;
            updateDashboard(data);
            console.log('Dashboard updated:', data);
        })
        .catch(error => console.error('Error fetching data:', error));
}

/**
 * Update entire dashboard with new data
 */
function updateDashboard(data) {
    // Update status
    updateStatus(data);

    // Update metrics
    updateMetrics(data);

    // Update markets
    updateMarkets(data);

    // Update positions
    updatePositions(data);

    // Update performance
    updatePerformance(data);

    // Update alerts
    updateAlerts(data);

    // Update charts
    updateCharts(data);

    // Update footer timestamp
    document.getElementById('last-update').textContent = formatTime(new Date());

    // Pulse refresh indicator
    const indicator = document.getElementById('refresh-indicator');
    if (indicator) {
        indicator.style.animation = 'none';
        setTimeout(() => {
            indicator.style.animation = 'spin 1s linear infinite';
        }, 10);
    }
}

/**
 * Update system status
 */
function updateStatus(data) {
    const statusBadge = document.getElementById('status-badge');
    const statusText = document.getElementById('status-text');
    const nextTrade = document.getElementById('next-trade');

    if (statusText) {
        statusText.textContent = data.system_status || 'READY';
    }

    if (nextTrade) {
        nextTrade.textContent = `Next: ${data.next_scan || '09:00 AM'}`;
    }

    // Set badge color based on status
    if (statusBadge) {
        if (data.system_status === 'RUNNING') {
            statusBadge.style.borderColor = '#10b981';
        } else if (data.system_status === 'ERROR') {
            statusBadge.style.borderColor = '#ef4444';
        } else {
            statusBadge.style.borderColor = '#f59e0b';
        }
    }
}

/**
 * Update key metrics
 */
function updateMetrics(data) {
    const capital = data.capital || {};

    updateElement('total-capital', formatCurrency(capital.total || 100000));
    updateElement('allocated-capital', formatCurrency(capital.allocated || 0));
    updateElement('reserve-capital', formatCurrency(capital.reserve || 100000));
    updateElement('monthly-return', formatPercent(data.performance?.monthly_return || 0, true));
}

/**
 * Update market rankings
 */
function updateMarkets(data) {
    const container = document.getElementById('markets-container');
    if (!container) return;

    const markets = (data.markets || []).sort((a, b) => b.quality - a.quality);

    if (markets.length === 0) {
        container.innerHTML = '<div class="market-row loading"><span>No market data</span></div>';
        return;
    }

    container.innerHTML = markets.map(market => `
        <div class="market-row">
            <div class="market-symbol">${market.symbol}</div>
            <div class="market-regime regime-${market.regime?.toLowerCase().replace(/[_\s]/g, '')}">
                ${formatRegime(market.regime)}
            </div>
            <div class="market-quality">${(market.quality || 0).toFixed(2)}</div>
            <div class="market-allocation">${formatPercent(market.allocation || 0)}</div>
        </div>
    `).join('');
}

/**
 * Update open positions
 */
function updatePositions(data) {
    const container = document.getElementById('positions-container');
    const countBadge = document.getElementById('position-count');

    if (!container) return;

    const positions = data.positions || [];

    if (countBadge) {
        countBadge.textContent = positions.length;
    }

    if (positions.length === 0) {
        container.innerHTML = `
            <div class="no-positions">
                <p>No open positions</p>
                <span>Waiting for trading signals...</span>
            </div>
        `;
        return;
    }

    container.innerHTML = positions.map(pos => {
        const pnlClass = pos.pnl >= 0 ? 'positive' : 'negative';
        const typeClass = pos.type?.toLowerCase() === 'long' ? 'long' : 'short';

        return `
            <div class="position-item">
                <div class="position-header">
                    <div class="position-symbol">${pos.symbol} ${pos.timeframe || 'daily'}</div>
                    <div class="position-type">${pos.type || 'LONG'}</div>
                </div>
                <div class="position-details">
                    <div class="position-detail">
                        <span>Entry:</span>
                        <span>$${(pos.entry_price || 0).toFixed(2)}</span>
                    </div>
                    <div class="position-detail">
                        <span>Current:</span>
                        <span>$${(pos.current_price || 0).toFixed(2)}</span>
                    </div>
                    <div class="position-detail">
                        <span>Size:</span>
                        <span>${(pos.quantity || 0).toFixed(4)} units</span>
                    </div>
                    <div class="position-detail">
                        <span>Stop:</span>
                        <span>$${(pos.stop_loss || 0).toFixed(2)}</span>
                    </div>
                </div>
                <div class="position-pnl ${pnlClass}">
                    ${pos.pnl >= 0 ? '+' : ''}$${(pos.pnl || 0).toFixed(0)} (${(pos.pnl_pct || 0).toFixed(2)}%)
                </div>
            </div>
        `;
    }).join('');
}

/**
 * Update performance metrics
 */
function updatePerformance(data) {
    const perf = data.performance || {};

    updateElement('total-trades', perf.total_trades || 0);
    updateElement('win-rate', formatPercent(perf.win_rate || 0));
    updateElement('sharpe-ratio', (perf.sharpe_ratio || 0).toFixed(2));

    const drawdownEl = document.getElementById('max-drawdown');
    if (drawdownEl) {
        drawdownEl.textContent = formatPercent(perf.max_drawdown || 0);
        drawdownEl.className = 'metric-number negative';
    }

    updateElement('win-loss-ratio', `${perf.winning_trades || 0} / ${perf.losing_trades || 0}`);

    const pnlEl = document.getElementById('monthly-pnl');
    if (pnlEl) {
        const pnl = perf.monthly_pnl || 0;
        pnlEl.textContent = formatCurrency(pnl);
        pnlEl.className = pnl >= 0 ? 'metric-number' : 'metric-number negative';
    }
}

/**
 * Update alerts
 */
function updateAlerts(data) {
    const container = document.getElementById('alerts-container');
    if (!container) return;

    const alerts = (data.alerts || []).slice(0, 10); // Last 10 alerts

    if (alerts.length === 0) {
        container.innerHTML = `
            <div class="alert-item info">
                <span class="alert-time">System ready</span>
                <span class="alert-message">Waiting for trading signals...</span>
            </div>
        `;
        return;
    }

    container.innerHTML = alerts.map(alert => `
        <div class="alert-item ${alert.type || 'info'}">
            <span class="alert-time">${formatTime(new Date(alert.time))}</span>
            <span class="alert-message">${alert.message}</span>
        </div>
    `).join('');
}

/**
 * Update all charts with live data
 */
function updateCharts(data) {
    updateQualityChart(data);
    updateAllocationChart(data);
    updatePerformanceChart(data);
    updateWinRateChart(data);
    updateRegimeChart(data);
    updateTradeResultsChart(data);
}

function updateQualityChart(data) {
    if (!charts.quality) return;

    const markets = data.markets || [];
    const labels = markets.map(m => m.symbol.replace('-USD', '').replace('=X', ''));
    const values = markets.map(m => m.quality || 0);
    const colors = ['#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4'];

    charts.quality.data = {
        labels: labels.length > 0 ? labels : ['No data'],
        datasets: [{
            label: 'Quality Score',
            data: values.length > 0 ? values : [0],
            backgroundColor: colors.slice(0, labels.length),
            borderColor: '#1f2937',
            borderWidth: 1,
        }]
    };

    charts.quality.update('none');
}

function updateAllocationChart(data) {
    if (!charts.allocation) return;

    const capital = data.capital || {};
    const markets = (data.markets || []).filter(m => m.allocation > 0);
    const labels = markets.map(m => m.symbol);
    const values = markets.map(m => m.allocation);
    const colors = ['#10b981', '#f59e0b', '#3b82f6', '#ef4444', '#8b5cf6', '#ec4899'];

    charts.allocation.data = {
        labels: labels.length > 0 ? labels : ['No allocation'],
        datasets: [{
            data: values.length > 0 ? values : [100],
            backgroundColor: colors.slice(0, labels.length),
            borderColor: '#1f2937',
            borderWidth: 2,
        }]
    };

    charts.allocation.update('none');
}

function updatePerformanceChart(data) {
    if (!charts.performance) return;

    const perf = data.performance || {};
    const return_val = perf.monthly_return || 0;

    // Simulate weekly progression
    const currentEquity = 100000 * (1 + return_val / 100);
    const week1 = 100000;
    const week2 = 100000 + (currentEquity - 100000) * 0.25;
    const week3 = 100000 + (currentEquity - 100000) * 0.6;
    const week4 = currentEquity;

    charts.performance.data.datasets[0].data = [week1, week2, week3, week4];
    charts.performance.update('none');
}

function updateWinRateChart(data) {
    if (!charts.winRate) return;

    const perf = data.performance || {};
    const wins = perf.winning_trades || 0;
    const losses = perf.losing_trades || 0;

    charts.winRate.data = {
        labels: [`Wins (${wins})`, `Losses (${losses})`],
        datasets: [{
            data: [wins, losses],
            backgroundColor: ['#10b981', '#ef4444'],
            borderColor: '#1f2937',
            borderWidth: 2,
        }]
    };

    charts.winRate.update('none');
}

function updateRegimeChart(data) {
    if (!charts.regime) return;

    const markets = data.markets || [];
    const regimes = ['STRONG_UPTREND', 'UPTREND', 'NEUTRAL', 'DOWNTREND', 'STRONG_DOWNTREND'];
    const counts = regimes.map(r =>
        markets.filter(m => m.regime?.toUpperCase().includes(r.split('_')[0])).length
    );

    charts.regime.data.datasets[0].data = counts;
    charts.regime.update('none');
}

function updateTradeResultsChart(data) {
    if (!charts.tradeResults) return;

    const perf = data.performance || {};
    const total = perf.total_trades || 1;
    const avgWin = total > 0 ? (perf.monthly_pnl / perf.winning_trades) || 0 : 0;
    const avgLoss = total > 0 ? (perf.monthly_pnl / perf.losing_trades) || 0 : 0;
    const sharpe = perf.sharpe_ratio || 0;
    const maxDD = Math.abs(perf.max_drawdown || 0);

    charts.tradeResults.data.datasets[0].data = [
        Math.abs(avgWin),
        Math.abs(avgLoss),
        sharpe,
        maxDD
    ];

    charts.tradeResults.update('none');
}

/**
 * Initialize all Chart.js charts
 */
function initializeCharts() {
    initializeQualityChart();
    initializeAllocationChart();
    initializePerformanceChart();
    initializeWinRateChart();
    initializeRegimeChart();
    initializeTradeResultsChart();
}

/**
 * Chart 1: Market Quality Scores (Bar Chart)
 */
function initializeQualityChart() {
    const ctx = document.getElementById('qualityChart');
    if (!ctx) return;

    charts.quality = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['BTC', 'ETH', 'SPY', 'QQQ', 'EUR', 'GLD'],
            datasets: [{
                label: 'Quality Score',
                data: [0, 0, 0, 0, 0, 0],
                backgroundColor: [
                    '#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4'
                ],
                borderColor: '#1f2937',
                borderWidth: 1,
                borderRadius: 6,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { display: false },
                tooltip: chartTooltipOptions()
            },
            scales: {
                x: { max: 1.0, ticks: { color: '#d1d5db' }, grid: { color: '#374151' } },
                y: { ticks: { color: '#d1d5db' } }
            }
        }
    });
}

/**
 * Chart 2: Capital Allocation (Pie Chart)
 */
function initializeAllocationChart() {
    const ctx = document.getElementById('allocationChart');
    if (!ctx) return;

    charts.allocation = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Loading...'],
            datasets: [{
                data: [100],
                backgroundColor: ['#6b7280'],
                borderColor: '#1f2937',
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    position: 'right',
                    labels: {
                        color: '#d1d5db',
                        font: { size: 12, weight: '600' },
                        padding: 15,
                    }
                },
                tooltip: chartTooltipOptions()
            }
        }
    });
}

/**
 * Chart 3: Performance Over Time (Line Chart)
 */
function initializePerformanceChart() {
    const ctx = document.getElementById('performanceChart');
    if (!ctx) return;

    charts.performance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: ['Week 1', 'Week 2', 'Week 3', 'Week 4'],
            datasets: [{
                label: 'Equity Growth',
                data: [100000, 101200, 103400, 105800],
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                fill: true,
                borderWidth: 2,
                pointBackgroundColor: '#10b981',
                pointBorderColor: '#1f2937',
                pointBorderWidth: 2,
                pointRadius: 5,
                tension: 0.4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { labels: { color: '#d1d5db' } },
                tooltip: chartTooltipOptions()
            },
            scales: {
                y: {
                    ticks: { color: '#d1d5db', callback: (v) => `$${v/1000}k` },
                    grid: { color: '#374151' }
                },
                x: {
                    ticks: { color: '#d1d5db' },
                    grid: { color: '#374151' }
                }
            }
        }
    });
}

/**
 * Chart 4: Win Rate (Doughnut Chart)
 */
function initializeWinRateChart() {
    const ctx = document.getElementById('winRateChart');
    if (!ctx) return;

    charts.winRate = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Wins', 'Losses'],
            datasets: [{
                data: [0, 0],
                backgroundColor: ['#10b981', '#ef4444'],
                borderColor: '#1f2937',
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { labels: { color: '#d1d5db', font: { weight: '600' } } },
                tooltip: chartTooltipOptions()
            }
        }
    });
}

/**
 * Chart 5: Market Regime Distribution (Radar Chart)
 */
function initializeRegimeChart() {
    const ctx = document.getElementById('regimeChart');
    if (!ctx) return;

    charts.regime = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: ['STRONG_UP', 'UPTREND', 'NEUTRAL', 'DOWNTREND', 'RANGING'],
            datasets: [{
                label: 'Markets in Regime',
                data: [0, 0, 0, 0, 0],
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.2)',
                pointBackgroundColor: '#10b981',
                pointBorderColor: '#1f2937',
                pointBorderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { labels: { color: '#d1d5db' } },
                tooltip: chartTooltipOptions()
            },
            scales: {
                r: {
                    ticks: { color: '#d1d5db' },
                    grid: { color: '#374151' }
                }
            }
        }
    });
}

/**
 * Chart 6: Trade Results Distribution (Bar Chart)
 */
function initializeTradeResultsChart() {
    const ctx = document.getElementById('tradeResultsChart');
    if (!ctx) return;

    charts.tradeResults = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Average Win', 'Average Loss', 'Sharpe', 'Max DD'],
            datasets: [{
                label: 'Value',
                data: [0, 0, 0, 0],
                backgroundColor: ['#10b981', '#ef4444', '#f59e0b', '#3b82f6'],
                borderColor: '#1f2937',
                borderWidth: 1,
                borderRadius: 6,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            indexAxis: 'y',
            plugins: {
                legend: { display: false },
                tooltip: chartTooltipOptions()
            },
            scales: {
                x: { ticks: { color: '#d1d5db' }, grid: { color: '#374151' } },
                y: { ticks: { color: '#d1d5db' } }
            }
        }
    });
}

/**
 * Common tooltip options for all charts
 */
function chartTooltipOptions() {
    return {
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        titleColor: '#10b981',
        bodyColor: '#d1d5db',
        borderColor: '#374151',
        borderWidth: 1,
        padding: 12,
        titleFont: { weight: '700', size: 13 },
        bodyFont: { size: 12 },
        displayColors: true,
        callbacks: {
            label: (context) => {
                let value = context.parsed.y || context.parsed || 0;
                if (context.chart.type === 'doughnut' && context.chart.data.labels[0] !== 'Wins') {
                    return `$${value.toLocaleString()}`;
                }
                return value.toFixed(2);
            }
        }
    };
}

/**
 * Update clock display
 */
function updateClock() {
    const clockEl = document.getElementById('current-time');
    if (clockEl) {
        clockEl.textContent = formatTime(new Date());
    }
}

/* ============================================
   UTILITY FUNCTIONS
   ============================================ */

/**
 * Update element text content
 */
function updateElement(id, value) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = value;
    }
}

/**
 * Format currency
 */
function formatCurrency(value) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(value);
}

/**
 * Format percentage
 */
function formatPercent(value, showSign = false) {
    const sign = showSign && value > 0 ? '+' : '';
    return `${sign}${value.toFixed(2)}%`;
}

/**
 * Format time HH:MM AM/PM
 */
function formatTime(date) {
    return date.toLocaleString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

/**
 * Format regime name
 */
function formatRegime(regime) {
    if (!regime) return '---';
    return regime
        .split('_')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
        .join(' ')
        .substring(0, 15);
}

/**
 * Log to console with timestamp
 */
function log(message) {
    console.log(`[${new Date().toLocaleTimeString()}] ${message}`);
}

// Start monitoring
log('Dashboard monitoring active');
