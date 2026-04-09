<template>
  <div class="research-notebook-page">
    <div v-if="isBootstrapping" class="state-shell">
      <div class="state-card">
        <div class="state-title">正在打开研究工作台</div>
        <p class="state-text">正在连接 Notebook 会话，请稍候。</p>
      </div>
    </div>

    <div v-else-if="!hasNotebookSession" class="state-shell">
      <div class="state-card">
        <div class="state-title">Notebook 会话</div>
        <p class="state-text">{{ stateMessage }}</p>
        <button class="state-action" type="button" :disabled="sessionLoading" @click="bootstrap(false)">
          {{ sessionLoading ? '连接中...' : '重新连接' }}
        </button>
      </div>
    </div>

    <div v-else class="iframe-shell" :class="{ 'is-loading': iframeLoading }">
      <transition name="loading-fade">
        <div v-if="iframeLoading" class="iframe-loading-mask">
          <div class="loading-card">
            <div class="loading-spinner" aria-hidden="true"></div>
            <div class="loading-title">Notebook 正在启动</div>
            <p class="loading-text">Jupyter 前端初始化通常需要一点时间，请稍候。</p>
          </div>
        </div>
      </transition>
      <iframe
        :key="`notebook-frame-${frameNonce}`"
        ref="notebookFrame"
        class="notebook-iframe"
        :src="notebookUrl"
        frameborder="0"
        @load="handleIframeLoad"
      />
    </div>

    <transition name="toast">
      <div v-if="showToast" class="toast" :class="toastType">
        <span>{{ toastMessage }}</span>
      </div>
    </transition>
  </div>
</template>

<script>
import {
  getResearch,
  createResearch,
  createNotebookSession
} from '@/api/research';

const DEFAULT_RESEARCH_ID = 'research_workbench';
const DEFAULT_RESEARCH_TITLE = '研究工作台';
const DEFAULT_KERNEL = 'python3';
const DEFAULT_NOTEBOOK_PATH = 'research/notebooks/research_workbench.ipynb';

function normalizeTags(raw) {
  if (Array.isArray(raw)) {
    return raw.map((item) => String(item || '').trim()).filter(Boolean);
  }
  if (typeof raw === 'string') {
    return raw
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

function normalizeResearch(payload, fallbackId = DEFAULT_RESEARCH_ID) {
  const raw = payload && typeof payload === 'object' ? payload : {};
  const root = raw.research && typeof raw.research === 'object' ? raw.research : raw;
  return {
    id: String(root.id || root.research_id || fallbackId || '').trim(),
    title: String(root.title || root.name || DEFAULT_RESEARCH_TITLE).trim(),
    description: String(root.description || root.desc || '').trim(),
    notebook_path: String(root.notebook_path || root.notebookPath || root.path || '').trim(),
    kernel: String(root.kernel || root.kernel_name || DEFAULT_KERNEL).trim(),
    status: String(root.status || 'ACTIVE').trim().toUpperCase(),
    tags: normalizeTags(root.tags),
    created_at: root.created_at || root.createdAt || root.ctime || '',
    updated_at: root.updated_at || root.updatedAt || root.mtime || ''
  };
}

function normalizeSession(payload) {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const root = payload.session && typeof payload.session === 'object'
    ? payload.session
    : (payload.notebook_session && typeof payload.notebook_session === 'object'
      ? payload.notebook_session
      : payload);

  const sessionId = String(root.session_id || root.id || '').trim();
  const notebookUrl = String(
    root.notebook_url
      || root.url
      || root.jupyter_url
      || root.launch_url
      || root.open_url
      || ''
  ).trim();
  const embedUrl = String(root.iframe_url || root.embed_url || root.notebook_iframe_url || '').trim();

  if (!sessionId && !notebookUrl && !embedUrl) {
    return null;
  }

  return {
    session_id: sessionId,
    notebook_url: notebookUrl,
    embed_url: embedUrl,
    status: String(root.status || '').trim().toUpperCase(),
    started_at: root.started_at || root.created_at || '',
    last_active_at: root.last_active_at || root.updated_at || '',
    expires_at: root.expires_at || ''
  };
}



export default {
  name: 'ResearchNotebook',
  data() {
    return {
      research: {
        id: DEFAULT_RESEARCH_ID,
        title: DEFAULT_RESEARCH_TITLE,
        description: '',
        notebook_path: '',
        kernel: DEFAULT_KERNEL,
        status: 'ACTIVE',
        tags: [],
        created_at: '',
        updated_at: ''
      },
      session: null,
      sessionLoading: false,
      isBootstrapping: true,
      iframeLoading: false,
      iframeReady: false,
      frameNonce: 0,
      iframePathRecoveryTried: false,
      iframeReadyTimer: null,
      bootstrapError: '',
      showToast: false,
      toastType: 'success',
      toastMessage: '',
      toastTimer: null
    };
  },
  computed: {
    researchId() {
      return DEFAULT_RESEARCH_ID;
    },
    notebookUrl() {
      return this.session?.embed_url || this.session?.notebook_url || '';
    },
    hasNotebookSession() {
      return Boolean(this.notebookUrl);
    },
    hasSessionRecord() {
      return Boolean(this.session?.session_id);
    },
    stateMessage() {
      if (this.bootstrapError) {
        return this.bootstrapError;
      }
      if (this.sessionLoading) {
        return '正在建立 Notebook 会话，请稍候。';
      }
      if (this.hasSessionRecord) {
        return '会话已创建，正在等待 Notebook 地址。';
      }
      return '当前没有可用的 Notebook 会话。';
    }
  },
  methods: {
    showMessage(message, type = 'success') {
      this.toastType = type;
      this.toastMessage = message;
      this.showToast = true;
      if (this.toastTimer) {
        clearTimeout(this.toastTimer);
      }
      this.toastTimer = setTimeout(() => {
        this.showToast = false;
      }, 2200);
    },
    getErrorMessage(error, fallback) {
      if (error && error.response && error.response.data) {
        const data = error.response.data;
        if (typeof data === 'string') {
          return data;
        }
        if (data.message) {
          return data.message;
        }
        if (data.error && typeof data.error === 'string') {
          return data.error;
        }
        if (data.error && typeof data.error === 'object' && data.error.message) {
          return data.error.message;
        }
      }
      return (error && error.message) || fallback;
    },
    buildSessionPayload() {
      const payload = {};
      const kernel = String(this.research.kernel || DEFAULT_KERNEL).trim();
      if (kernel) {
        payload.kernel = kernel;
      }
      payload.notebook_path = DEFAULT_NOTEBOOK_PATH;
      return payload;
    },
    isNotebookPathIssue(message) {
      const text = String(message || '').toLowerCase();
      if (!text) {
        return false;
      }
      return (
        text.includes('cannot open')
        || text.includes('could not find path')
        || text.includes('notebook_path')
        || text.includes('路径')
        || text.includes('not found')
      );
    },
    async ensureResearchExists() {
      try {
        const data = await getResearch(this.researchId);
        this.research = normalizeResearch(data, this.researchId);
        return;
      } catch (error) {
        const status = Number(error?.response?.status || 0);
        if (status !== 404) {
          throw error;
        }
      }

      try {
        const created = await createResearch({
          id: this.researchId,
          title: DEFAULT_RESEARCH_TITLE,
          description: '',
          notebook_path: DEFAULT_NOTEBOOK_PATH,
          kernel: DEFAULT_KERNEL,
          status: 'ACTIVE',
          tags: []
        });
        this.research = normalizeResearch(created, this.researchId);
      } catch (error) {
        const status = Number(error?.response?.status || 0);
        if (status !== 409) {
          throw error;
        }
        const data = await getResearch(this.researchId);
        this.research = normalizeResearch(data, this.researchId);
      }
    },
    async requestSessionWithFallback() {
      const firstPayload = this.buildSessionPayload();
      try {
        const data = await createNotebookSession(this.researchId, firstPayload);
        return normalizeSession(data);
      } catch (error) {
        const message = this.getErrorMessage(error, '');
        if (!this.isNotebookPathIssue(message)) {
          throw error;
        }
        const fallbackPayload = this.buildSessionPayload();
        const data = await createNotebookSession(this.researchId, fallbackPayload);
        return normalizeSession(data);
      }
    },
    async recoverSessionAfterPathError() {
      try {
        const session = await this.requestSessionWithFallback();
        if (!session) {
          return false;
        }
        this.session = session;
        this.iframePathRecoveryTried = false;
        this.iframeReady = false;
        this.frameNonce += 1;
        this.iframeLoading = !!this.notebookUrl;
        return this.hasNotebookSession;
      } catch (error) {
        return false;
      }
    },
    sleep(ms) {
      return new Promise((resolve) => {
        setTimeout(resolve, ms);
      });
    },
    async waitForNotebookUrl(maxAttempts = 8, intervalMs = 1000) {
      for (let i = 0; i < maxAttempts; i += 1) {
        if (this.hasNotebookSession) {
          return true;
        }
        await this.sleep(intervalMs);
        try {
          const latest = await this.requestSessionWithFallback();
          if (latest) {
            this.session = latest;
          }
          if (this.hasNotebookSession) {
            return true;
          }
        } catch (error) {
          const message = this.getErrorMessage(error, '');
          if (this.isNotebookPathIssue(message)) {
            const recovered = await this.recoverSessionAfterPathError();
            if (recovered) {
              return true;
            }
          }
        }
      }
      return this.hasNotebookSession;
    },
    async bootstrap(showErrorToast = false) {
      this.sessionLoading = true;
      this.isBootstrapping = true;
      this.bootstrapError = '';
      try {
        await this.ensureResearchExists();
        this.session = await this.requestSessionWithFallback();
        if (!this.session) {
          throw new Error('后端未返回会话信息');
        }
        if (!this.hasNotebookSession) {
          const ready = await this.waitForNotebookUrl(10, 1000);
          if (!ready) {
            throw new Error('会话已创建，但后端尚未返回可用的 Notebook 地址');
          }
        }
        this.frameNonce += 1;
        this.iframePathRecoveryTried = false;
        this.iframeReady = false;
        this.iframeLoading = !!this.notebookUrl;
      } catch (error) {
        const message = this.getErrorMessage(error, '启动会话失败');
        this.bootstrapError = message;
        if (showErrorToast) {
          this.showMessage(message, 'error');
        }
      } finally {
        this.sessionLoading = false;
        this.isBootstrapping = false;
      }
    },
    isNotebookUiReady() {
      const frame = this.$refs.notebookFrame;
      if (!frame) {
        return false;
      }
      try {
        const frameDocument = frame.contentDocument || frame.contentWindow?.document;
        if (!frameDocument) {
          return false;
        }
        const body = frameDocument.body;
        if (!body) {
          return false;
        }

        const hasLabShell = Boolean(
          frameDocument.querySelector('.jp-LabShell')
          || frameDocument.querySelector('#jp-main-dock-panel')
          || frameDocument.querySelector('.jp-FileBrowser')
          || frameDocument.querySelector('.jp-Launcher')
          || frameDocument.querySelector('.jp-NotebookPanel')
        );
        if (hasLabShell) {
          return true;
        }

        const bodyText = String(body.innerText || '').trim();
        if (bodyText && bodyText.length > 20 && !/loading|starting|kernel/i.test(bodyText)) {
          return true;
        }
      } catch (error) {
        return true;
      }
      return false;
    },
    clearIframeReadyTimer() {
      if (this.iframeReadyTimer) {
        clearInterval(this.iframeReadyTimer);
        this.iframeReadyTimer = null;
      }
    },
    beginIframeReadyWatch() {
      this.clearIframeReadyTimer();
      const startedAt = Date.now();
      const tick = () => {
        if (!this.hasNotebookSession) {
          this.clearIframeReadyTimer();
          return;
        }
        if (this.isNotebookUiReady()) {
          this.iframeReady = true;
          this.iframeLoading = false;
          this.clearIframeReadyTimer();
          return;
        }
        if (Date.now() - startedAt >= 30000) {
          this.iframeReady = true;
          this.iframeLoading = false;
          this.clearIframeReadyTimer();
        }
      };
      tick();
      this.iframeReadyTimer = setInterval(tick, 300);
    },
    async handleIframeLoad() {
      this.iframeReady = false;
      this.iframeLoading = true;
      this.beginIframeReadyWatch();
      if (this.iframePathRecoveryTried || !this.hasNotebookSession) {
        return;
      }
      const frame = this.$refs.notebookFrame;
      if (!frame) {
        return;
      }
      let pageText = '';
      try {
        const frameDocument = frame.contentDocument || frame.contentWindow?.document;
        pageText = String(
          frameDocument?.body?.innerText
          || frameDocument?.documentElement?.innerText
          || ''
        ).toLowerCase();
      } catch (error) {
        return;
      }
      if (!this.isNotebookPathIssue(pageText)) {
        return;
      }
      this.iframePathRecoveryTried = true;
      this.showMessage('检测到 Notebook 路径异常，正在重建会话...');
      const recovered = await this.recoverSessionAfterPathError();
      if (recovered) {
        this.showMessage('路径异常已恢复，已使用新的会话地址');
        return;
      }
      this.bootstrapError = 'Notebook 路径异常，重建会话失败';
      this.showMessage(this.bootstrapError, 'error');
    }
  },
  async mounted() {
    await this.bootstrap(false);
  },
  beforeUnmount() {
    this.clearIframeReadyTimer();
    if (this.toastTimer) {
      clearTimeout(this.toastTimer);
      this.toastTimer = null;
    }
  }
};
</script>

<style scoped>
.research-notebook-page {
  position: relative;
  height: calc(100vh - 72px);
  min-height: 560px;
  overflow: hidden;
  border-radius: 16px;
  background:
    radial-gradient(circle at top left, rgba(227, 242, 253, 0.95), rgba(227, 242, 253, 0) 30%),
    linear-gradient(180deg, #f8fbff 0%, #eef4f8 100%);
  border: 1px solid rgba(148, 163, 184, 0.22);
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.08);
}

.iframe-shell {
  position: relative;
  width: 100%;
  height: 100%;
  background: rgba(255, 255, 255, 0.72);
  overflow: hidden;
}

.notebook-iframe {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
  opacity: 1;
  transition: opacity 0.18s ease;
}

.iframe-shell.is-loading .notebook-iframe {
  opacity: 0;
}

.iframe-loading-mask {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(241, 245, 249, 0.96)),
    radial-gradient(circle at top left, rgba(191, 219, 254, 0.55), rgba(191, 219, 254, 0) 35%);
  backdrop-filter: blur(2px);
}

.loading-card {
  width: min(420px, 100%);
  padding: 28px 24px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.24);
  box-shadow: 0 22px 60px rgba(15, 23, 42, 0.12);
  text-align: center;
}

.loading-spinner {
  width: 44px;
  height: 44px;
  margin: 0 auto 16px;
  border-radius: 999px;
  border: 3px solid rgba(148, 163, 184, 0.3);
  border-top-color: #0f172a;
  animation: spin 0.9s linear infinite;
}

.loading-title {
  color: #0f172a;
  font-size: 18px;
  font-weight: 600;
}

.loading-text {
  margin: 10px 0 0;
  color: #475569;
  font-size: 14px;
  line-height: 1.7;
}

.state-shell {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 24px;
}

.state-card {
  width: min(440px, 100%);
  padding: 28px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.22);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.1);
  text-align: center;
}

.state-title {
  margin-bottom: 10px;
  color: #0f172a;
  font-size: 18px;
  font-weight: 600;
}

.state-text {
  margin: 0;
  color: #475569;
  font-size: 14px;
  line-height: 1.7;
}

.state-action {
  margin-top: 18px;
  padding: 10px 16px;
  border: 0;
  border-radius: 999px;
  background: #0f172a;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  transition: opacity 0.18s ease;
}

.state-action:disabled {
  opacity: 0.65;
  cursor: default;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.loading-fade-enter-active,
.loading-fade-leave-active {
  transition: opacity 0.22s ease;
}

.loading-fade-enter-from,
.loading-fade-leave-to {
  opacity: 0;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.toast {
  position: absolute;
  right: 20px;
  bottom: 20px;
  z-index: 3;
  padding: 10px 14px;
  border-radius: 12px;
  color: #fff;
  font-size: 13px;
  box-shadow: 0 12px 36px rgba(15, 23, 42, 0.2);
}

.toast.success {
  background: rgba(22, 101, 52, 0.92);
}

.toast.error {
  background: rgba(185, 28, 28, 0.92);
}

@media (max-width: 768px) {
  .research-notebook-page {
    height: calc(100vh - 64px);
    min-height: 480px;
    border-radius: 12px;
  }

  .state-shell {
    padding: 16px;
  }

  .state-card {
    padding: 22px 18px;
    border-radius: 16px;
  }

  .iframe-loading-mask,
  .toast {
    right: 14px;
    left: 14px;
    width: auto;
  }

  .loading-card {
    padding: 24px 18px;
    border-radius: 16px;
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
