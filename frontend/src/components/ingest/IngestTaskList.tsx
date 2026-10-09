import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { listIngestJobs } from '@/services/ingest'
import type { IngestJobListItem } from '@/types/api'

interface IngestTaskListProps {
  instanceId: string | null
  refreshToken?: number
}

/**
 * 展示当前实例最近写入任务和完成指标。
 *
 * @param props 当前实例和刷新令牌
 * @returns 写入任务列表
 */
export function IngestTaskList({ instanceId, refreshToken = 0 }: IngestTaskListProps) {
  const { t } = useTranslation('ingest')
  const [items, setItems] = useState<IngestJobListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!instanceId) return
    let cancelled = false
    const load = () => {
      setLoading(true)
      listIngestJobs(instanceId)
        .then((data) => { if (!cancelled) { setItems(data.items); setFailed(false) } })
        .catch(() => { if (!cancelled) setFailed(true) })
        .finally(() => { if (!cancelled) setLoading(false) })
    }
    load()
    const interval = window.setInterval(load, 5000)
    return () => { cancelled = true; window.clearInterval(interval) }
  }, [instanceId, refreshToken])

  return (
    <div className="bg-card rounded-lg border border-border p-4">
      <h3 className="mb-3 font-medium">{t('taskList.title')}</h3>
      {loading && items.length === 0 && <p className="text-sm text-muted-foreground">{t('taskList.loading')}</p>}
      {failed && <p className="text-sm text-warning">{t('taskList.failed')}</p>}
      {!loading && !failed && items.length === 0 && <p className="text-sm text-muted-foreground">{t('taskList.empty')}</p>}
      <div className="divide-y divide-border">
        {items.map((item) => (
          <div key={item.job_id} className="grid gap-2 py-3 text-sm md:grid-cols-[minmax(0,1fr)_100px_repeat(3,70px)_110px]">
            <span className="truncate">{item.input_file || item.job_id}</span>
            <span>{item.status}</span>
            <span>{t('taskList.metricsCards')}: {item.metrics.card_count}</span>
            <span>{t('taskList.metricsEntities')}: {item.metrics.entity_candidate_count}</span>
            <span>{t('taskList.metricsRelations')}: {item.metrics.relation_count}</span>
            <span className="text-xs text-muted-foreground">{item.metrics.dedup_mode === 'semantic' ? t('taskList.dedupSemantic') : item.metrics.dedup_mode === 'rule' ? t('taskList.dedupRule') : t('taskList.dedupDisabled')}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
