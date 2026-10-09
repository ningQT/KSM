import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AccountManager } from '@/components/accounts/AccountManager'
import { ModelConfigForm } from '@/components/settings/ModelConfigForm'
import { ModelConfigList } from '@/components/settings/ModelConfigList'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/useAuthStore'
import type { ModelCategory } from '@/types/api'

/**
 * 系统设置页面，包含模型类别配置和账户管理。
 *
 * @returns 设置页内容
 */
export default function SettingsPage() {
  const { t } = useTranslation('settings')
  const user = useAuthStore((state) => state.user)
  const [activeSection, setActiveSection] = useState<'model' | 'accounts'>('model')
  const [modelCategory, setModelCategory] = useState<ModelCategory>('llm')
  const [listRevision, setListRevision] = useState(0)

  if (user?.role !== 'admin') {
    return <div className="rounded-lg border border-border bg-card p-6 text-sm text-muted-foreground">{t('forbidden')}</div>
  }

  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h2 className="text-xl font-semibold">{t('title')}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t('description')}</p>
      </div>

      <div className="flex gap-2">
        <Button variant={activeSection === 'model' ? 'default' : 'secondary'} onClick={() => setActiveSection('model')}>{t('tabs.model')}</Button>
        <Button variant={activeSection === 'accounts' ? 'default' : 'secondary'} onClick={() => setActiveSection('accounts')}>{t('tabs.accounts')}</Button>
      </div>

      {activeSection === 'model' ? (
        <div className="space-y-6">
          <div>
            <p className="mb-2 text-sm font-medium">{t('category.label')}</p>
            <div className="inline-flex rounded-md border border-border bg-muted/30 p-1">
              {(['llm', 'embedding'] as const).map((category) => (
                <Button key={category} type="button" variant={modelCategory === category ? 'default' : 'ghost'} onClick={() => setModelCategory(category)}>
                  {t(`category.${category}`)}
                </Button>
              ))}
            </div>
          </div>
          <ModelConfigForm key={`${modelCategory}-${listRevision}`} category={modelCategory} onChanged={() => setListRevision((value) => value + 1)} />
          <ModelConfigList key={`list-${listRevision}`} />
        </div>
      ) : (
        <AccountManager />
      )}
    </div>
  )
}
