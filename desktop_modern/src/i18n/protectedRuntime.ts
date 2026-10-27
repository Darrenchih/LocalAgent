const en = {
  title: 'Protected runtime', managementOnly: 'Management Only', executionDisabled: 'Execution Disabled',
  safety: 'Ready means the management connection is healthy only. Formal tool execution remains disabled.',
  disabled: 'Disabled', starting: 'Starting', ready: 'Ready (management healthy only)', stopping: 'Stopping', stopped: 'Stopped', failed: 'Failed', unknown: 'Status unavailable',
  start: 'Start', refresh: 'Refresh', stop: 'Stop', bundle: 'Bundle', version: 'Artifact version',
  requestError: 'Unable to verify a safe management status. Refresh to try again.', runtimeError: 'Management error',
};
export const protectedRuntimeTranslations = {
  en,
  'zh-TW': {
    title: '受保護執行環境', managementOnly: '僅限管理', executionDisabled: '執行已停用',
    safety: '已就緒僅表示管理連線正常。正式工具執行維持停用。',
    disabled: '已停用', starting: '啟動中', ready: '已就緒（僅管理連線正常）', stopping: '停止中', stopped: '已停止', failed: '失敗', unknown: '無法取得狀態',
    start: '啟動', refresh: '重新整理', stop: '停止', bundle: '套件', version: '產物版本',
    requestError: '無法確認安全的管理狀態。請重新整理以重試。', runtimeError: '管理錯誤',
  } satisfies typeof en,
  'zh-CN': {
    title: '受保护运行环境', managementOnly: '仅限管理', executionDisabled: '执行已停用',
    safety: '已就绪仅表示管理连接正常。正式工具执行保持停用。',
    disabled: '已停用', starting: '启动中', ready: '已就绪（仅管理连接正常）', stopping: '停止中', stopped: '已停止', failed: '失败', unknown: '无法获取状态',
    start: '启动', refresh: '刷新', stop: '停止', bundle: '包', version: '产物版本',
    requestError: '无法确认安全的管理状态。请刷新重试。', runtimeError: '管理错误',
  } satisfies typeof en,
};
