import { Database } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { DataExplorer } from './DataExplorer'

export function DataExplorerDialog() {
  return <Dialog><DialogTrigger asChild><Button variant="ghost" size="sm"><Database />采集结果</Button></DialogTrigger><DialogContent className="flex h-[85vh] max-w-[95vw] flex-col sm:max-w-6xl"><DialogHeader><DialogTitle>本地数据文件</DialogTitle><DialogDescription>预览、下载和管理采集文件。</DialogDescription></DialogHeader><div className="min-h-0 flex-1 overflow-auto"><DataExplorer /></div></DialogContent></Dialog>
}
