import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getModelConfigList } from '@/services/settings'
import type { ModelConfigList as ModelConfigListType } from '@/types/api'

/**
 * 展示 LLM 和 embedding 当前配置摘要。
 *
 * @returns 只读模型配置列表
 */
export function ModelConfigList() {
  const { t } = useTranslation('settings')
  const [data, setData] = useState<ModelConfigListType | null>(null)

  useEffect(() => {
    void getModelConfigList().then(setData).catch(() => setData({ items: [], provider_options: [] }))
  }, [])

  return (
    <div className="bg-card rounded-lg border border-border p-6">
      <h3 className="font-medium">{t('list.title')}</h3>
      <div className="mt-4 divide-y divide-border rounded-md border border-border">
        {data?.items.map((item) => (
          <div key={item.category} className="grid gap-2 px-3 py-3 text-sm md:grid-cols-4">
            <span className="font-medium">{t(`category.${item.category}`)}</span>
            <span>{item.provider || '-'}</span>
            <span className="font-mono text-xs">{item.model || '-'}</span>
            <span className={item.configured ? 'text-success' : 'text-muted-foreground'}>
              {item.configured ? t('list.configured') : t('list.unconfigured')}
            </span>
          </div>
        ))}
        {data && data.items.length === 0 && <p className="p-3 text-sm text-muted-foreground">{t('list.empty')}</p>}
      </div>
    </div>
  )
}
