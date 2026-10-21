import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 180000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Types
export interface CrawlerConfig {
  platform: string
  platforms: string[]
  login_type: string
  crawler_type: string
  keywords: string
  start_page: number
  enable_comments: boolean
  enable_sub_comments: boolean
  save_option: string
  cookies: string
  headless: boolean
}

export interface CrawlerStatus {
  status: 'idle' | 'running' | 'stopping' | 'error'
  platform: string | null
  crawler_type: string | null
  started_at: string | null
  error_message: string | null
}

export interface LogEntry {
  id: number
  timestamp: string
  level: 'info' | 'warning' | 'error' | 'success' | 'debug'
  message: string
}

export interface DataFile {
  name: string
  path: string
  size: number
  modified_at: number
  record_count: number | null
  type: string
}

export interface FilePreviewResponse {
  data: Record<string, unknown>[]
  total: number
  columns?: string[]
}

export interface AnalysisResult {
  output_file: string
  input_count: number
  output_count: number
  scope_excluded?: number
  ai_enabled: boolean
  batch_size?: number
  batch_summaries?: { batch: number; record_count: number; summary?: string; topics?: string[] }[]
  intelligence?: {
    topics: { topic: string; content_count: number; platforms: Record<string, number>; representative_terms: string[]; recent_count: number; trend: string; evidence_count: number; quality_score: number }[]
    research_directions: { research_direction: string; score: number; topic: string; why_now: string; research_questions: string[]; recommended_methods: string[]; evidence_topics: string[] }[]
  }
}

export interface BatchAnalysisResponse {
  completed: number
  failed: number
  results: { path: string; status: 'completed' | 'failed'; error?: string; result?: AnalysisResult }[]
}

export interface Platform {
  value: string
  label: string
  icon: string
}

export interface ConfigOption {
  value: string
  label: string
}

// API functions
export const crawlerApi = {
  start: (config: CrawlerConfig) => api.post('/crawler/start', config),
  stop: () => api.post('/crawler/stop'),
  getStatus: () => api.get<CrawlerStatus>('/crawler/status'),
  getLogs: (limit = 100) => api.get<{ logs: LogEntry[] }>('/crawler/logs', { params: { limit } }),
}

export const dataApi = {
  getFiles: (platform?: string, fileType?: string, includeAnalysis = true) =>
    api.get<{ files: DataFile[] }>('/data/files', { params: { platform, file_type: fileType, include_analysis: includeAnalysis } }),
  getFileContent: (path: string, limit = 100) =>
    api.get<FilePreviewResponse>('/data/files/' + path, { params: { preview: true, limit } }),
  deleteFile: (path: string) => api.delete<{ deleted: boolean; name: string }>('/data/files/' + path),
  getStats: () => api.get('/data/stats'),
  getDownloadUrl: (path: string) => `/api/data/download/${path}`,
  analyze: (path: string, selectedThemes: string[], customThemes: string[], limit = 500) => api.post<{ source_file: string; output_file: string; input_count: number; output_count: number; scope_excluded?: number; selected_themes?: string[]; ai_enabled: boolean; batch_size?: number; batch_summaries?: { batch: number; record_count: number; summary?: string; topics?: string[]; claims?: string[] }[]; intelligence?: { topics: { topic: string; content_count: number; platforms: Record<string, number>; representative_terms: string[]; recent_count: number; trend: string; evidence_count: number; quality_score: number }[]; research_directions: { research_direction: string; score: number; topic: string; why_now: string; research_questions: string[]; recommended_methods: string[]; evidence_topics: string[] }[] } }>('/data/analyze', { path, limit, selected_themes: selectedThemes, custom_themes: customThemes }),
  analyzeMany: async (paths: string[], selectedThemes: string[], customThemes: string[], limit = 500, merge = false) => {
    try {
      return await api.post<BatchAnalysisResponse>('/data/analyze/batch', { paths, limit, selected_themes: selectedThemes, custom_themes: customThemes, merge })
    } catch (error) {
      // Existing running backends may not have the batch route yet.  Fall back
      // to the established single-file endpoint so multi-select still works.
      if (!axios.isAxiosError(error) || error.response?.status !== 404) throw error
      const results: BatchAnalysisResponse['results'] = []
      for (const path of paths) {
        try {
          const response = await api.post<AnalysisResult>('/data/analyze', { path, limit, selected_themes: selectedThemes, custom_themes: customThemes })
          results.push({ path, status: 'completed', result: response.data })
        } catch (itemError) {
          const detail = axios.isAxiosError(itemError) ? String(itemError.response?.data?.detail || itemError.message) : String(itemError)
          results.push({ path, status: 'failed', error: detail })
        }
      }
      const completed = results.filter((item) => item.status === 'completed').length
      return { data: { results, completed, failed: results.length - completed } }
    }
  },
  getAnalysisConfig: () => api.get<{ models: { value: string; label: string }[]; model: string; ai_enabled: boolean; themes: string[] }>('/data/analysis-config'),
  setAnalysisModel: (model: string) => api.put<{ model: string }>('/data/analysis-config', { model }),
}

export const configApi = {
  getPlatforms: () => api.get<{ platforms: Platform[] }>('/config/platforms'),
  getOptions: () =>
    api.get<{
      login_types: ConfigOption[]
      crawler_types: ConfigOption[]
      save_options: ConfigOption[]
    }>('/config/options'),
}

export type VaultKind = 'academic' | 'social'
export interface DeepTutorKnowledgeBase {
  name: string
  connected: boolean
  status: string
  documents: number
  progress?: { stage?: string; message?: string } | null
  url: string
}
export interface DeepTutorStatus {
  available: boolean
  reason?: string
  knowledge_bases: Partial<Record<VaultKind, DeepTutorKnowledgeBase>>
  vaults: ObsidianVault[]
}
export const deeptutorApi = {
  status: () => api.get<DeepTutorStatus>('/deeptutor/status'),
  sync: (kind: VaultKind) => api.post<{ state: string; new_files: number; modified_files: number }>(`/deeptutor/sync/${kind}`),
}

// AI Scientist bridge types
export interface ScientistCheck {
  name: string
  ok: boolean
  message: string
  severity?: 'error' | 'warning' | 'info'
}

export interface ScientistOverview {
  root: string
  python: string
  ready: boolean
  counts: {
    records: number
    trends: number
    candidates: number
    production_tasks: number
  }
  checks: ScientistCheck[]
}

export interface ObsidianVault {
  kind: 'academic' | 'social'
  path: string
  available: boolean
  note_count: number
}

export interface ObsidianVaultNote {
  path: string
  title: string
  meta: Record<string, string>
  modified_at: number
}

export interface ScientistPreflight {
  ready: boolean
  root: string
  python: string
  workspace: string
  checks: ScientistCheck[]
  errors: ScientistCheck[]
  warnings: ScientistCheck[]
}

export interface IdeaPreview {
  index: number
  name: string
  title: string
}

export interface IdeaFile {
  file: string
  name: string
  count: number
  ideas: IdeaPreview[]
}

export interface ProductionTask {
  id: string
  candidate_title: string
  mode: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  idea_file?: string
  command?: string[]
  pid?: number | null
  exit_code?: number | null
  error?: string | null
  log_path?: string
  base_project?: string
}

export interface GeneratedIdea {
  index: number
  name: string
  title: string
  hypothesis: string
  abstract: string
  related_work: string
  experiments: string
  risks: string
  source: string
  score: number
  evidence: string[]
}

export interface GeneratedIdeasResult {
  file: string
  name: string
  count: number
  method: 'ai' | 'rules'
  strategy: 'v2_reflection' | 'v2_novelty'
  basis: IdeaBasis
  ideas: GeneratedIdea[]
}

export interface IdeaBasis {
  theme: string
  topic_alignment?: { topic_id: string; label: string; taxonomy_version: string; match_method: string }
  fusion_scores?: { ai_scientist_academic: number; mediacrawler_evidence: number; combined: number; weights: { ai_scientist_academic: number; mediacrawler_evidence: number } }
  strategy_label?: string
  counts: { trends: number; records: number; candidates: number; directions: number; topics: number }
  cards: { kind: string; title: string; detail: string; url?: string }[]
  note: string
  openalex?: { title: string; year: string; url: string }[]
  workshop?: { path: string; reference_count: number; references: string[] }
  knowledge_base?: { path: string; papers: { title: string; path: string }[]; note: string; auto_update?: { status: string; query: string; imported: number; skipped: number; reason?: string } }
  fallback_reason?: string
}

export interface IdeaStrategy {
  value: 'v2_reflection' | 'v2_novelty'
  label: string
  description: string
}

export interface BaseProjectRecommendation {
  full_name: string
  url: string
  description: string
  stars: number
  language: string
  updated_at: string
  matched_terms: string[]
  score: number
}

export const scientistApi = {
  overview: () => api.get<ScientistOverview>('/scientist/overview'),
  preflight: () => api.get<ScientistPreflight>('/scientist/preflight'),
  ideas: () => api.get<{ files: IdeaFile[] }>('/scientist/ideas'),
  ideaRecommendations: (ideaFile: string, ideaIdx: number) => api.get<{ query: string; terms: string[]; repositories: BaseProjectRecommendation[]; warning: string }>('/scientist/idea-recommendations', { params: { idea_file: ideaFile, idea_idx: ideaIdx } }),
  deleteIdea: (ideaFile: string) => api.delete<{ deleted: boolean; name: string }>('/scientist/ideas', { params: { idea_file: ideaFile } }),
  generateIdeas: (payload: { count: number; theme?: string; strategy: 'v2_reflection' | 'v2_novelty'; reflections: number; allow_rule_fallback: boolean }) => api.post<GeneratedIdeasResult>('/scientist/ideas/generate', payload),
  ideaThemes: () => api.get<{ themes: string[] }>('/scientist/idea-themes'),
  ideaStrategies: () => api.get<{ strategies: IdeaStrategy[] }>('/scientist/idea-strategies'),
  ideaBasisPreview: (theme?: string) => api.get<IdeaBasis>('/scientist/idea-basis-preview', { params: { theme } }),
  records: (limit = 100) => api.get<{ records: Record<string, unknown>[] }>('/scientist/records', { params: { limit } }),
  trends: (limit = 100) => api.get<{ trends: Record<string, unknown>[] }>('/scientist/trends', { params: { limit } }),
  candidates: (limit = 100) => api.get<{ candidates: Record<string, unknown>[] }>('/scientist/candidates', { params: { limit } }),
  startProduction: (payload: { idea_file: string; idea_idx: number; mode: string; attempt_id: number; base_project?: string }) =>
    api.post<ProductionTask>('/scientist/production', payload),
  productionTasks: () => api.get<{ tasks: ProductionTask[] }>('/scientist/production'),
  cancelProductionTask: (taskId: string) => api.post<ProductionTask>(`/scientist/production/${taskId}/cancel`),
  retryProductionTask: (taskId: string) => api.post<ProductionTask>(`/scientist/production/${taskId}/retry`),
  deleteProductionTask: (taskId: string) => api.delete<{ deleted: boolean; id: string }>(`/scientist/production/${taskId}`),
  productionLog: (taskId: string) => api.get<{ log: string }>(`/scientist/production/${taskId}/log`),
  vaultStatus: () => api.get<{ path: string; available: boolean; note_count: number }>('/scientist/vault/status'),
  setVaultPath: (path: string) => api.put<{ path: string; available: boolean; note_count: number }>('/scientist/vault/config', { path }),
  vaultNotes: () => api.get<{ notes: { path: string; title: string; meta: Record<string, string>; modified_at: number }[] }>('/scientist/vault/notes'),
  vaultNote: (path: string) => api.get<{ path: string; content: string; meta: Record<string, string> }>('/scientist/vault/note', { params: { path } }),
  renameVaultReport: (path: string, title: string) => api.put<{ path: string; title: string }>('/scientist/vault/report', { path, title }),
  deleteVaultReport: (path: string) => api.delete<{ deleted: boolean; name: string }>('/scientist/vault/report', { params: { path } }),
  syncObsidian: () => api.post('/scientist/sync-obsidian'),
  vaultsStatus: () => api.get<{ vaults: ObsidianVault[] }>('/scientist/vaults/status'),
  configureVaults: (academicPath: string, socialPath: string) => api.put<{ vaults: ObsidianVault[] }>('/scientist/vaults/config', { academic_path: academicPath, social_path: socialPath }),
  syncVaults: () => api.post('/scientist/vaults/sync'),
  vaultNotesByKind: (kind: ObsidianVault['kind'], limit = 5000) => api.get<{ notes: ObsidianVaultNote[] }>(`/scientist/vaults/${kind}/notes`, { params: { limit } }),
  vaultNoteByKind: (kind: ObsidianVault['kind'], path: string) => api.get<{ path: string; content: string; meta: Record<string, string> }>(`/scientist/vaults/${kind}/note`, { params: { path } }),
}

export interface EnvCheckResult {
  success: boolean
  message: string
  output?: string
  error?: string
}

export const envApi = {
  check: () => api.get<EnvCheckResult>('/env/check'),
}

export default api
