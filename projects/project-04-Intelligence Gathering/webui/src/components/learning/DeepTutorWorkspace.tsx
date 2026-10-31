import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { deeptutorApi, type DeepTutorKnowledgeBase, type ObsidianVault } from '@/lib/api'

const labels = { academic: '学术资料', social: '社交情报与分析报告' }

function SourceCard({ vault, kb, available }: { vault: ObsidianVault; kb?: DeepTutorKnowledgeBase; available: boolean }) {
  const queryClient = useQueryClient()
  const [message, setMessage] = useState('')
  const sync = useMutation({
    mutationFn: () => deeptutorApi.sync(vault.kind),
    onSuccess: ({ data }) => setMessage(data.state === 'unchanged' ? '没有新增或修改文档。' : `已提交：新增 ${data.new_files}，修改 ${data.modified_files}。请等待索引完成。`),
    onError: (error) => setMessage(axios.isAxiosError(error) ? String(error.response?.data?.detail || error.message) : '同步失败，请稍后重试。'),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['deeptutor-status'] }),
  })
  const stage = kb?.progress?.stage
  const busy = sync.isPending || ['initializing', 'processing', 'indexing'].includes(kb?.status || '') || !!(stage && !['completed', 'error', 'idle'].includes(stage))
  const state = !vault.available ? '04目录不可用' : busy ? '正在处理…' : kb?.status === 'ready' ? '已就绪' : kb?.status === 'error' ? '同步失败，可重试' : '尚未关联'

  return <section className="min-w-0 rounded-xl border border-white/10 bg-[#202020] p-5">
    <h2 className="text-lg font-medium">{labels[vault.kind]}</h2>
    <p className="mt-3 text-sm text-zinc-400">04归档 {vault.note_count} 份 · 03已导入 {kb?.documents ?? 0} 份</p>
    <p className="mt-3 text-sm text-cyan-200" role="status">{state}</p>
    {busy && kb?.progress?.message && <p className="mt-2 text-xs text-zinc-400">{kb.progress.message}</p>}
    <div className="mt-5 flex flex-wrap gap-3">
      <Button onClick={() => sync.mutate()} disabled={!available || !vault.available || busy}><RefreshCw className={busy ? 'animate-spin' : ''} />{busy ? '处理中…' : '同步到03'}</Button>
      {kb?.connected && <a href={kb.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 rounded-md border border-white/15 px-3 py-2 text-sm text-zinc-200 hover:border-cyan-400/50">在03打开<ExternalLink className="h-4 w-4" /></a>}
    </div>
    {message && <p className="mt-4 text-sm text-amber-200" role="status">{message}</p>}
  </section>
}

export function DeepTutorWorkspace() {
  const status = useQuery({ queryKey: ['deeptutor-status'], queryFn: async () => (await deeptutorApi.status()).data, refetchInterval: 5000 })
  return <div className="mx-auto max-w-5xl space-y-6">
    <div><h1 className="text-2xl font-semibold">学习问答</h1><p className="mt-3 text-sm leading-7 text-zinc-400">将04采集、分析后归档的资料同步到03，在知识库中阅读、提问和学习。</p></div>
    <div className="rounded-xl border border-white/10 bg-[#202020] p-4 text-sm" role="status">{status.isPending ? '正在检查03连接…' : status.isError ? '连接检查失败，请刷新或检查04服务。' : status.data.available ? '03 已连接 · 对话和知识库入口可访问' : status.data.reason}</div>
    <div className="grid gap-5 lg:grid-cols-2">{status.data?.vaults.map(vault => <SourceCard key={vault.kind} vault={vault} kb={status.data.knowledge_bases[vault.kind]} available={status.data.available} />)}</div>
    <div className="space-y-3 border-l-2 border-zinc-600 pl-4 text-sm leading-7 text-zinc-400"><p>先在04完成采集、分析与归档，再点击同步。等待“已就绪”后，在03对话的“资源”中选择对应知识库。</p><p>同步复制新增或修改的文档，04原文保持不变。后续资料更新需再次同步；源文件删除不会自动从03删除。</p><p>来源卡可能只有摘要和元数据，不等同于论文全文。语音、图像与视频生成仍需单独配置。</p></div>
  </div>
}
