<template>
  <div class="overview-container">
    <div v-if="loading" class="loading-state">
      <p>加载中...</p>
    </div>

    <div v-else-if="error" class="error-state">
      <div class="inline-error">{{ error }}</div>
      <button @click="loadOverview" class="btn btn-secondary">重试</button>
    </div>

    <div v-else-if="!data.analyzed" class="empty-state">
      <div v-if="analyzing || currentTaskId" class="analyzing-message">
        <div class="spinner"></div>
        <p>行情数据分析中，请稍等...</p>
      </div>
      <div v-else>
        <p>{{ data.message }}</p>
        <button @click="triggerAnalyze" class="btn btn-primary">
          触发数据分析
        </button>
      </div>
    </div>

    <div v-else class="content-layout">
      <div class="main-panel">
        <div class="panel-header">
          <h3>数据概览</h3>
          <button @click="triggerAnalyze" class="btn btn-secondary btn-mini" :disabled="analyzing">
            {{ analyzing ? '分析中...' : '重新分析' }}
          </button>
        </div>
        <div class="panel-body">
          <div class="stats-grid">
            <div class="stat-item">
              <div class="stat-label">分析时间</div>
              <div class="stat-value">{{ formatDate(data.data.analyzed_at) }}</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">总文件数</div>
              <div class="stat-value">{{ data.data.total_files }}</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">总大小</div>
              <div class="stat-value">{{ formatSize(data.data.total_size_bytes) }}</div>
            </div>
            <div class="stat-item">
              <div class="stat-label">数据更新时间</div>
              <div class="stat-value">{{ formatDate(data.data.last_modified) }}</div>
            </div>
          </div>

          <div class="data-section">
            <div class="data-table-wrapper">
              <h4 class="section-title">行情数量-分品种</h4>
              <table class="data-table">
                <thead>
                  <tr>
                    <th style="width: 60px; text-align: center">#</th>
                    <th>品种</th>
                    <th style="text-align: center">数量</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td class="row-number">1</td>
                    <td>股票</td>
                    <td class="text-center mono">{{ formatNumber(data.data.stock_count) }}</td>
                  </tr>
                  <tr>
                    <td class="row-number">2</td>
                    <td>基金</td>
                    <td class="text-center mono">{{ formatNumber(data.data.fund_count) }}</td>
                  </tr>
                  <tr>
                    <td class="row-number">3</td>
                    <td>期货</td>
                    <td class="text-center mono">{{ formatNumber(data.data.futures_count) }}</td>
                  </tr>
                  <tr>
                    <td class="row-number">4</td>
                    <td>指数</td>
                    <td class="text-center mono">{{ formatNumber(data.data.index_count) }}</td>
                  </tr>
                  <tr>
                    <td class="row-number">5</td>
                    <td>债券</td>
                    <td class="text-center mono">{{ formatNumber(data.data.bond_count) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="chart-wrapper">
              <h4 class="section-title">行情分布饼图</h4>
              <div class="chart-container">
                <canvas ref="chartCanvas"></canvas>
              </div>
            </div>
            <div class="files-list-wrapper">
              <h4 class="section-title">数据文件列表</h4>
              <div class="files-table-container">
                <div v-if="!files || files.length === 0" class="empty-files">
                  <p>暂无文件信息</p>
                </div>
                <table v-else class="files-table">
                  <thead>
                    <tr>
                      <th style="width: 40px; text-align: center">#</th>
                      <th>文件名</th>
                      <th style="width: 80px; text-align: right">大小</th>
                      <th style="width: 120px; text-align: center">数据更新时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(file, index) in files" :key="index">
                      <td class="row-number">{{ index + 1 }}</td>
                      <td class="file-name">{{ file.file_name }}</td>
                      <td class="file-size">{{ formatSize(file.file_size) }}</td>
                      <td class="file-modified">{{ formatFileDate(file.modified_at) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <!-- 数据质量报告 -->
          <div class="inspection-section">
            <div class="panel-header">
              <h3>数据质量报告</h3>
              <button @click="loadInspection" class="btn btn-secondary btn-mini" :disabled="inspectionLoading">
                {{ inspectionLoading ? '加载中...' : '刷新报告' }}
              </button>
            </div>
            <div v-if="inspectionLoading" class="loading-state">
              <p>加载数据质量报告中...</p>
            </div>
            <div v-else-if="inspectionError" class="error-state">
              <div class="inline-error">{{ inspectionError }}</div>
              <button @click="loadInspection" class="btn btn-secondary">重试</button>
            </div>
            <div v-else-if="inspectionData" class="inspection-content">
              <div class="inspection-stats">
                <div class="stat-item">
                  <div class="stat-label">总股票数</div>
                  <div class="stat-value">{{ formatNumber(inspectionData.stats.total_stocks) }}</div>
                </div>
                <div class="stat-item">
                  <div class="stat-label">总记录数</div>
                  <div class="stat-value">{{ formatNumber(inspectionData.stats.total_records) }}</div>
                </div>
                <div class="stat-item">
                  <div class="stat-label">数据频率</div>
                  <div class="stat-value">{{ Object.keys(inspectionData.stats.frequencies).join(', ') }}</div>
                </div>
              </div>
              
              <div class="inspection-charts">
                <div class="chart-item">
                  <h4 class="section-title">数据完整性矩阵</h4>
                  <div class="chart-container">
                    <img :src="'data:image/png;base64,' + inspectionData.visualizations.completeness_matrix" alt="数据完整性矩阵">
                  </div>
                </div>
                <div class="chart-item">
                  <h4 class="section-title">数据量分布直方图</h4>
                  <div class="chart-container">
                    <img :src="'data:image/png;base64,' + inspectionData.visualizations.distribution_histogram" alt="数据量分布直方图">
                  </div>
                </div>
                <div class="chart-item">
                  <h4 class="section-title">更新时效性散点图</h4>
                  <div class="chart-container">
                    <img :src="'data:image/png;base64,' + inspectionData.visualizations.timeliness_scatter" alt="更新时效性散点图">
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="empty-state">
              <p>暂无数据质量报告</p>
              <button @click="loadInspection" class="btn btn-secondary">生成报告</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <TaskProgress v-if="currentTaskId" :task-id="currentTaskId" @task-complete="handleTaskComplete" />
  </div>
</template>

<script>
import TaskProgress from '@/components/MarketData/TaskProgress.vue';

export default {
  name: 'MarketDataOverview',
  components: {
    TaskProgress
  },
  data() {
    return {
      loading: false,
      error: null,
      data: {},
      files: [],
      analyzing: false,
      currentTaskId: null,
      autoTriggered: false,
      inspectionLoading: false,
      inspectionError: null,
      inspectionData: null
    };
  },
  mounted() {
    this.loadOverview();
  },
  beforeUnmount() {
    // No chart library to destroy
  },
  watch: {
    'data.analyzed': {
      handler(newVal) {
        if (newVal) {
          this.$nextTick(() => {
            // Double-check canvas is available
            if (this.$refs.chartCanvas) {
              this.renderChart();
            } else {
              // Retry after another tick if canvas not ready
              setTimeout(() => {
                if (this.$refs.chartCanvas) {
                  this.renderChart();
                }
              }, 100);
            }
          });
        }
      },
      immediate: true
    }
  },
  methods: {
    async loadOverview() {
      this.loading = true;
      this.error = null;

      try {
        const response = await fetch('/api/market-data/overview', {
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('token')}`
          }
        });

        if (response.ok) {
          const result = await response.json();
          this.data = result;
          // Sort files by size (largest to smallest)
          this.files = (result.files || []).sort((a, b) => b.file_size - a.file_size);
          if (this.data.analyzed) {
            this.$nextTick(() => {
              this.renderChart();
            });
          } else if (!this.autoTriggered) {
            // 首次加载且没有数据时，自动触发数据分析
            this.autoTriggered = true;
            this.triggerAnalyze(true);
          }
        } else {
          this.error = '加载数据失败';
        }
      } catch (err) {
        this.error = '网络错误，请重试';
      } finally {
        this.loading = false;
      }
    },
    async triggerAnalyze(isAuto = false) {
      this.analyzing = true;

      try {
        const response = await fetch('/api/market-data/analyze', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token')}`
          },
          body: JSON.stringify({
            source: isAuto ? 'auto' : 'manual'
          })
        });

        if (response.ok) {
          const result = await response.json();
          this.currentTaskId = result.task_id;
        } else if (response.status === 409) {
          alert('已有任务正在运行，请等待完成');
        } else {
          alert('触发分析失败');
        }
      } catch (err) {
        alert('网络错误，请重试');
      } finally {
        this.analyzing = false;
      }
    },
    handleTaskComplete(task) {
      this.currentTaskId = null;
      if (task.status === 'success') {
        this.loadOverview();
      }
    },
    async loadInspection() {
      this.inspectionLoading = true;
      this.inspectionError = null;

      try {
        const response = await fetch('/api/market-data/inspection', {
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('token')}`
          }
        });

        if (response.ok) {
          const result = await response.json();
          this.inspectionData = result;
        } else {
          const errorData = await response.json();
          this.inspectionError = errorData.error || '加载数据质量报告失败';
        }
      } catch (err) {
        this.inspectionError = '网络错误，请重试';
      } finally {
        this.inspectionLoading = false;
      }
    },
    renderChart() {
      const canvas = this.$refs.chartCanvas;
      if (!canvas || !this.data.data) return;

      // Scale canvas for high DPI displays
      const dpr = window.devicePixelRatio || 1;
      const displayWidth = 450;
      const displayHeight = 300;

      canvas.width = displayWidth * dpr;
      canvas.height = displayHeight * dpr;
      canvas.style.width = displayWidth + 'px';
      canvas.style.height = displayHeight + 'px';

      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);

      const chartData = [
        { label: '股票', value: this.data.data.stock_count, color: '#1976d2' },
        { label: '基金', value: this.data.data.fund_count, color: '#388e3c' },
        { label: '期货', value: this.data.data.futures_count, color: '#f57c00' },
        { label: '指数', value: this.data.data.index_count, color: '#7b1fa2' },
        { label: '债券', value: this.data.data.bond_count, color: '#d32f2f' }
      ].filter(item => item.value > 0);

      if (chartData.length === 0) return;

      this.drawPieChart(ctx, chartData, displayWidth, displayHeight);
    },
    drawPieChart(ctx, data, width, height) {
      const pieWidth = 280;
      const centerX = pieWidth / 2;
      const centerY = height / 2;
      const radius = Math.min(pieWidth, height) / 2 - 40;
      const total = data.reduce((sum, item) => sum + item.value, 0);
      let currentAngle = -Math.PI / 2;

      // Draw pie slices
      data.forEach(item => {
        const sliceAngle = (item.value / total) * 2 * Math.PI;
        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        ctx.arc(centerX, centerY, radius, currentAngle, currentAngle + sliceAngle);
        ctx.closePath();
        ctx.fillStyle = item.color;
        ctx.fill();

        // Draw percentage label
        const midAngle = currentAngle + sliceAngle / 2;
        const labelX = centerX + Math.cos(midAngle) * (radius * 0.65);
        const labelY = centerY + Math.sin(midAngle) * (radius * 0.65);
        const percentage = ((item.value / total) * 100).toFixed(1);

        ctx.fillStyle = '#fff';
        ctx.font = 'bold 13px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(`${percentage}%`, labelX, labelY);

        currentAngle += sliceAngle;
      });

      // Draw legend on the right
      const legendX = pieWidth + 30;
      const legendStartY = (height - data.length * 30) / 2;

      data.forEach((item, index) => {
        const y = legendStartY + index * 30;

        // Draw color box
        ctx.fillStyle = item.color;
        ctx.fillRect(legendX, y, 12, 12);

        // Draw label
        ctx.fillStyle = '#000';
        ctx.font = '12px sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(item.label, legendX + 18, y + 6);
      });
    },
    formatDate(dateStr) {
      if (!dateStr) return '-';
      // 后端存储的是 UTC 时间，添加 Z 后缀让浏览器自动转换为本地时间
      const date = new Date(dateStr.endsWith('Z') ? dateStr : dateStr + 'Z');
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      const hours = String(date.getHours()).padStart(2, '0');
      const minutes = String(date.getMinutes()).padStart(2, '0');
      const seconds = String(date.getSeconds()).padStart(2, '0');
      return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
    },
    formatSize(bytes) {
      if (!bytes) return '-';
      const units = ['B', 'KB', 'MB', 'GB'];
      let size = bytes;
      let unitIndex = 0;
      while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024;
        unitIndex++;
      }
      // 如果数字大于等于10，不显示小数
      const decimals = size >= 10 ? 0 : 2;
      return `${size.toFixed(decimals)}${units[unitIndex]}`;
    },
    formatNumber(num) {
      if (!num) return '0';
      return num.toLocaleString('zh-CN');
    },
    formatFileDate(dateStr) {
      if (!dateStr) return '-';
      // 后端存储的是 UTC 时间，添加 Z 后缀让浏览器自动转换为本地时间
      const date = new Date(dateStr.endsWith('Z') ? dateStr : dateStr + 'Z');
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      const hours = String(date.getHours()).padStart(2, '0');
      const minutes = String(date.getMinutes()).padStart(2, '0');
      const seconds = String(date.getSeconds()).padStart(2, '0');
      return `${month}/${day} ${hours}:${minutes}:${seconds}`;
    }
  }
};
</script>

<style scoped>
.overview-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.loading-state,
.error-state,
.empty-state {
  padding: 40px;
  text-align: center;
}

.analyzing-message {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.analyzing-message p {
  margin: 0;
  color: #1976d2;
  font-size: 14px;
  font-weight: 500;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid rgba(25, 118, 210, 0.2);
  border-top-color: #1976d2;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.loading-state p,
.empty-state p {
  margin: 0 0 16px 0;
  color: #666;
  font-size: 12px;
}

.inline-error {
  background: #ffebee;
  border: 1px solid #ef5350;
  color: #c62828;
  padding: 8px 12px;
  border-radius: 2px;
  margin-bottom: 12px;
  font-size: 12px;
}

.content-layout {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.main-panel {
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
}

.panel-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #000;
}

.panel-body {
  padding: 16px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e0e0e0;
}

.stat-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-label {
  font-size: 11px;
  color: #666;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stat-value {
  font-size: 14px;
  color: #000;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-weight: 500;
}

.data-section {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  flex-wrap: wrap;
}

.section-title {
  margin: 0 0 12px 0;
  font-size: 13px;
  font-weight: 600;
  color: #000;
}

.data-table-wrapper {
  flex: 1 1 350px;
  min-width: 350px;
  max-width: 450px;
  min-height: 420px;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
}

.chart-wrapper {
  flex: 1 1 400px;
  min-width: 400px;
  min-height: 420px;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
}

.files-list-wrapper {
  flex: 1 1 450px;
  min-width: 450px;
  max-width: 650px;
  min-height: 420px;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
}

.files-table-container {
  max-height: 350px;
  overflow-y: auto;
  overflow-x: hidden;
}

.empty-files {
  padding: 40px 20px;
  text-align: center;
}

.empty-files p {
  margin: 0;
  color: #999;
  font-size: 12px;
}

.files-table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  overflow: hidden;
  font-size: 11px;
}

.files-table thead th {
  background: #fafafa;
  padding: 8px 12px;
  border-bottom: 1px solid #e0e0e0;
  font-size: 11px;
  font-weight: 600;
  color: #000;
  text-align: left;
  position: sticky;
  top: 0;
  z-index: 1;
}

.files-table tbody td {
  padding: 6px 12px;
  border-bottom: 1px solid #f0f0f0;
  color: #000;
}

.files-table tbody tr:last-child td {
  border-bottom: none;
}

.files-table tbody tr:hover {
  background: #f9f9f9;
}

.files-table .file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.files-table .file-size {
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: #666;
  white-space: nowrap;
}

.files-table .file-modified {
  text-align: center;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: #666;
  font-size: 11px;
  white-space: nowrap;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  overflow: hidden;
}

.data-table thead th {
  background: #fafafa;
  padding: 8px 12px;
  border-bottom: 1px solid #e0e0e0;
  font-size: 12px;
  font-weight: 600;
  color: #000;
  text-align: left;
}

.data-table tbody td {
  padding: 8px 12px;
  border-bottom: 1px solid #e0e0e0;
  font-size: 12px;
  color: #000;
}

.data-table tbody tr:last-child td {
  border-bottom: none;
}

.row-number {
  color: #999;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  text-align: center;
}

.text-center {
  text-align: center;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.chart-container {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
}

.chart-container canvas {
  display: block;
}

.btn {
  border: 1px solid #d0d0d0;
  background: #fff;
  color: #000;
  padding: 6px 12px;
  border-radius: 2px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.15s ease;
}

.btn:hover:not(:disabled) {
  border-color: #999;
  background: #f5f5f5;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-primary {
  background: #1976d2;
  color: #fff;
  border-color: #1976d2;
}

.btn-primary:hover:not(:disabled) {
  background: #1565c0;
  border-color: #1565c0;
}

.btn-secondary {
  background: #fff;
}

.btn-mini {
  padding: 4px 8px;
  font-size: 12px;
}

/* 数据质量报告样式 */
.inspection-section {
  margin-top: 24px;
  border-top: 1px solid #e0e0e0;
  padding-top: 16px;
}

.inspection-section .panel-header {
  margin-bottom: 16px;
}

.inspection-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e0e0e0;
}

.inspection-charts {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 16px;
}

.chart-item {
  flex: 1 1 300px;
  min-width: 300px;
  padding: 16px;
  background: #fafafa;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
}

.chart-item .chart-container {
  margin-top: 12px;
  overflow: hidden;
  border-radius: 4px;
}

.chart-item img {
  width: 100%;
  height: auto;
  display: block;
}

.inspection-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
