import type { ReactNode } from 'react'
import { useLang } from '../lib/i18n'

export function Loading({ label }: { label?: string }) {
  const { t } = useLang()
  return (
    <div className="loading">
      <span className="spinner" aria-hidden />
      <span>{label ?? t('loading')}</span>
    </div>
  )
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useLang()
  return (
    <div className="note note--danger">
      <div className="row row--between">
        <span>{message}</span>
        {onRetry && (
          <button type="button" className="btn btn--ghost" onClick={onRetry}>
            {t('retry')}
          </button>
        )}
      </div>
    </div>
  )
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      {hint && <div className="small">{hint}</div>}
    </div>
  )
}

export function Pill({
  tone = '',
  children,
  plain = false,
}: {
  tone?: 'ok' | 'warn' | 'danger' | 'info' | ''
  children: ReactNode
  plain?: boolean
}) {
  const classes = ['pill', tone && `pill--${tone}`, plain && 'pill--plain']
    .filter(Boolean)
    .join(' ')
  return <span className={classes}>{children}</span>
}

export function Stat({
  label,
  value,
  foot,
  tone,
}: {
  label: string
  value: ReactNode
  foot?: ReactNode
  tone?: 'alert' | 'good'
}) {
  return (
    <div className={`stat${tone ? ` stat--${tone}` : ''}`}>
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
      {foot && <div className="stat__foot">{foot}</div>}
    </div>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  )
}

export function Card({
  title,
  action,
  children,
  flush = false,
}: {
  title?: string
  action?: ReactNode
  children: ReactNode
  flush?: boolean
}) {
  return (
    <section className={`card${flush ? ' card--flush' : ''}`}>
      {title && (
        <div className="card__head" style={flush ? { padding: '18px 20px 0' } : undefined}>
          <h3>{title}</h3>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}
