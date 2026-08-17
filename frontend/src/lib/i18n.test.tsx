import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { LanguageProvider, useLang } from './i18n'

function Probe() {
  const { t, pick, lang, toggle } = useLang()
  return (
    <div>
      <span data-testid="lang">{lang}</span>
      <span data-testid="translated">{t('signIn')}</span>
      <span data-testid="picked">{pick('हिन्दी पाठ', 'English text')}</span>
      <button type="button" onClick={toggle}>
        toggle
      </button>
    </div>
  )
}

function renderProbe() {
  return render(
    <LanguageProvider>
      <Probe />
    </LanguageProvider>,
  )
}

describe('language', () => {
  it('defaults to Hindi', () => {
    // The people this system is for read Hindi first; English is the toggle.
    renderProbe()
    expect(screen.getByTestId('lang')).toHaveTextContent('hi')
    expect(screen.getByTestId('translated')).toHaveTextContent('लॉग इन करें')
  })

  it('toggles to English and back', async () => {
    const user = userEvent.setup()
    renderProbe()

    await user.click(screen.getByRole('button', { name: 'toggle' }))
    expect(screen.getByTestId('lang')).toHaveTextContent('en')
    expect(screen.getByTestId('translated')).toHaveTextContent('Sign in')

    await user.click(screen.getByRole('button', { name: 'toggle' }))
    expect(screen.getByTestId('translated')).toHaveTextContent('लॉग इन करें')
  })

  it('remembers the choice across a reload', async () => {
    const user = userEvent.setup()
    const first = renderProbe()
    await user.click(screen.getByRole('button', { name: 'toggle' }))
    first.unmount()

    renderProbe()
    expect(screen.getByTestId('lang')).toHaveTextContent('en')
  })

  it('sets the document language so screen readers pronounce correctly', async () => {
    const user = userEvent.setup()
    renderProbe()
    expect(document.documentElement.lang).toBe('hi')

    await user.click(screen.getByRole('button', { name: 'toggle' }))
    expect(document.documentElement.lang).toBe('en')
  })

  it('pick() chooses the half matching the current language', async () => {
    const user = userEvent.setup()
    renderProbe()
    expect(screen.getByTestId('picked')).toHaveTextContent('हिन्दी पाठ')

    await user.click(screen.getByRole('button', { name: 'toggle' }))
    expect(screen.getByTestId('picked')).toHaveTextContent('English text')
  })

  it('restores a previously stored language on mount', () => {
    localStorage.setItem('opd.lang', 'en')
    renderProbe()
    expect(screen.getByTestId('lang')).toHaveTextContent('en')
  })
})
