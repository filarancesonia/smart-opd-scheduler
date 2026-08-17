import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, deviceKey } from '../../lib/api'
import type { Department, Doctor, KioskTicket, Patient } from '../../lib/api'
import { useAction } from '../../lib/hooks'
import { useLang } from '../../lib/i18n'
import { addDays, clock, longDate, today } from '../../lib/format'
import './kiosk.css'

type Step =
  | 'setup'
  | 'welcome'
  | 'phone'
  | 'person'
  | 'newPerson'
  | 'department'
  | 'doctor'
  | 'date'
  | 'confirm'
  | 'ticket'

/** Return to the welcome screen if someone walks away mid-booking. */
const IDLE_RESET_MS = 90_000

export function Kiosk() {
  const { t, pick, lang, toggle } = useLang()
  const { busy, error, setError, run } = useAction()

  const [step, setStep] = useState<Step>(() => (deviceKey.get() ? 'welcome' : 'setup'))
  const [phone, setPhone] = useState('')
  const [matches, setMatches] = useState<Patient[]>([])
  const [person, setPerson] = useState<{ full_name: string; age: number | null } | null>(null)
  const [newName, setNewName] = useState('')
  const [newAge, setNewAge] = useState('')
  const [departments, setDepartments] = useState<Department[]>([])
  const [department, setDepartment] = useState<Department | null>(null)
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [doctor, setDoctor] = useState<Doctor | null>(null)
  const [date, setDate] = useState(today())
  const [ticket, setTicket] = useState<KioskTicket | null>(null)

  const reset = useCallback(() => {
    setStep(deviceKey.get() ? 'welcome' : 'setup')
    setPhone('')
    setMatches([])
    setPerson(null)
    setNewName('')
    setNewAge('')
    setDepartment(null)
    setDoctor(null)
    setDate(today())
    setTicket(null)
    setError(null)
  }, [setError])

  // Idle timeout — a half-finished booking left on screen is a privacy leak.
  useEffect(() => {
    if (step === 'welcome' || step === 'setup') return
    const timer = window.setTimeout(reset, IDLE_RESET_MS)
    const bump = () => {
      window.clearTimeout(timer)
    }
    window.addEventListener('pointerdown', bump, { once: true })
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('pointerdown', bump)
    }
  }, [step, phone, person, department, doctor, date, reset])

  const dateOptions = useMemo(
    () => [0, 1, 2].map((offset) => addDays(today(), offset)),
    [],
  )

  async function lookup() {
    const found = await run(() => api.kioskLookup(phone))
    if (found === null) return
    setMatches(found)
    setStep(found.length > 0 ? 'person' : 'newPerson')
  }

  async function loadDepartments() {
    const list = await run(() => api.departments())
    if (list) {
      setDepartments(list)
      setStep('department')
    }
  }

  async function loadDoctors(chosen: Department) {
    setDepartment(chosen)
    const list = await run(() => api.doctors(chosen.id))
    if (list) {
      setDoctors(list.filter((d) => d.is_accepting_patients))
      setStep('doctor')
    }
  }

  async function confirm() {
    if (!doctor || !person) return
    const created = await run(() =>
      api.kioskBook({
        doctor_id: doctor.id,
        appointment_date: date,
        patient: { full_name: person.full_name, phone, age: person.age },
      }),
    )
    if (created) {
      setTicket(created)
      setStep('ticket')
    }
  }

  return (
    <div className="kiosk">
      <div className="kiosk__bar">
        <span className="topbar__mark" aria-hidden>
          OPD
        </span>
        <span className="kiosk__title">{t('appName')}</span>
        <div className="spacer" />
        <button type="button" className="btn btn--ghost" onClick={toggle}
          style={{ background: 'rgb(255 255 255 / 15%)', color: '#fff', borderColor: 'transparent' }}>
          {t('language')}
        </button>
        {step !== 'welcome' && step !== 'setup' && (
          <button type="button" className="btn btn--ghost" onClick={reset}
            style={{ background: 'rgb(255 255 255 / 15%)', color: '#fff', borderColor: 'transparent' }}>
            {t('cancel')}
          </button>
        )}
      </div>

      <div className="kiosk__body">
        <div className="kiosk__panel">
          {error && (
            <div className="note note--danger" style={{ marginBottom: 20, fontSize: '1.05rem' }}>
              {error}
            </div>
          )}

          {step === 'setup' && <Setup onSaved={() => setStep('welcome')} />}

          {step === 'welcome' && (
            <>
              <div className="kiosk__step">{t('kioskWelcome')}</div>
              <h1 className="kiosk__question hi">
                {pick('क्या आप डॉक्टर से मिलना चाहते हैं?', 'Would you like to see a doctor?')}
              </h1>
              <p className="kiosk__sub hi">
                {pick(
                  'यहाँ अपॉइंटमेंट लेने के लिए स्मार्टफ़ोन की ज़रूरत नहीं है।',
                  'You do not need a smartphone to book here.',
                )}
              </p>
              <button
                type="button"
                className="btn btn--lg btn--block"
                style={{ minHeight: 96, fontSize: '1.5rem' }}
                onClick={() => setStep('phone')}
              >
                {t('kioskStart')}
              </button>
            </>
          )}

          {step === 'phone' && (
            <>
              <div className="kiosk__step">1 / 4</div>
              <h1 className="kiosk__question hi">{t('kioskEnterPhone')}</h1>
              <Keypad
                value={phone}
                onChange={setPhone}
                maxLength={10}
                placeholder="__________"
              />
              <div className="kiosk__actions">
                <button type="button" className="btn btn--ghost" onClick={reset}>
                  {t('back')}
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={phone.length !== 10 || busy}
                  onClick={lookup}
                >
                  {busy && <span className="spinner" aria-hidden />}
                  {t('next')}
                </button>
              </div>
            </>
          )}

          {step === 'person' && (
            <>
              <div className="kiosk__step">2 / 4</div>
              <h1 className="kiosk__question hi">{t('kioskWhoIsThis')}</h1>
              <div className="kiosk__choices">
                {matches.map((match) => (
                  <button
                    key={match.id}
                    type="button"
                    className="kiosk-choice"
                    onClick={() => {
                      setPerson({ full_name: match.full_name, age: match.age })
                      void loadDepartments()
                    }}
                  >
                    <span className="kiosk-choice__title">{match.full_name}</span>
                    <span className="kiosk-choice__sub">
                      {match.age ? `${match.age} ${pick('वर्ष', 'years')}` : ''}
                      {match.is_senior_citizen ? ` · ${pick('वरिष्ठ नागरिक', 'Senior citizen')}` : ''}
                    </span>
                  </button>
                ))}
                <button
                  type="button"
                  className="kiosk-choice"
                  onClick={() => setStep('newPerson')}
                >
                  <span className="kiosk-choice__title">+ {t('kioskNewPerson')}</span>
                </button>
              </div>
            </>
          )}

          {step === 'newPerson' && (
            <>
              <div className="kiosk__step">2 / 4</div>
              <h1 className="kiosk__question hi">{t('kioskYourName')}</h1>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={pick('पूरा नाम', 'Full name')}
                style={{ fontSize: '1.6rem', minHeight: 80, marginBottom: 18 }}
                autoFocus
              />
              <label style={{ fontSize: '1.15rem', fontWeight: 600 }}>{t('kioskYourAge')}</label>
              <input
                value={newAge}
                onChange={(e) => setNewAge(e.target.value.replace(/\D/g, '').slice(0, 3))}
                inputMode="numeric"
                placeholder="45"
                style={{ fontSize: '1.6rem', minHeight: 80, marginTop: 8 }}
              />
              <div className="kiosk__actions">
                <button type="button" className="btn btn--ghost" onClick={() => setStep('phone')}>
                  {t('back')}
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={newName.trim().length < 2 || busy}
                  onClick={() => {
                    setPerson({
                      full_name: newName.trim(),
                      age: newAge ? Number(newAge) : null,
                    })
                    void loadDepartments()
                  }}
                >
                  {t('next')}
                </button>
              </div>
            </>
          )}

          {step === 'department' && (
            <>
              <div className="kiosk__step">3 / 4</div>
              <h1 className="kiosk__question hi">{t('chooseDepartment')}</h1>
              <div className="kiosk__choices kiosk__choices--two">
                {departments.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="kiosk-choice"
                    onClick={() => void loadDoctors(item)}
                  >
                    <span className="kiosk-choice__title">{item.name}</span>
                    {item.floor && (
                      <span className="kiosk-choice__sub">
                        {pick('मंज़िल', 'Floor')} {item.floor}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </>
          )}

          {step === 'doctor' && (
            <>
              <div className="kiosk__step">3 / 4</div>
              <h1 className="kiosk__question hi">{t('chooseDoctor')}</h1>
              <div className="kiosk__choices">
                {doctors.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="kiosk-choice"
                    onClick={() => {
                      setDoctor(item)
                      setStep('date')
                    }}
                  >
                    <span className="kiosk-choice__title">{item.full_name}</span>
                    <span className="kiosk-choice__sub">
                      {item.specialisation || item.department_name}
                    </span>
                  </button>
                ))}
                {doctors.length === 0 && (
                  <p className="kiosk__sub hi">
                    {pick(
                      'इस विभाग में अभी कोई डॉक्टर उपलब्ध नहीं है।',
                      'No doctor is available in this department right now.',
                    )}
                  </p>
                )}
              </div>
              <div className="kiosk__actions">
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => setStep('department')}
                >
                  {t('back')}
                </button>
              </div>
            </>
          )}

          {step === 'date' && (
            <>
              <div className="kiosk__step">4 / 4</div>
              <h1 className="kiosk__question hi">{t('chooseDate')}</h1>
              <div className="kiosk__choices kiosk__choices--two">
                {dateOptions.map((value, index) => (
                  <button
                    key={value}
                    type="button"
                    className="kiosk-choice"
                    aria-pressed={date === value}
                    onClick={() => {
                      setDate(value)
                      setStep('confirm')
                    }}
                  >
                    <span className="kiosk-choice__title">
                      {index === 0 ? t('today') : index === 1 ? t('tomorrow') : longDate(value, lang)}
                    </span>
                    <span className="kiosk-choice__sub">{longDate(value, lang)}</span>
                  </button>
                ))}
              </div>
              <div className="kiosk__actions">
                <button type="button" className="btn btn--ghost" onClick={() => setStep('doctor')}>
                  {t('back')}
                </button>
              </div>
            </>
          )}

          {step === 'confirm' && doctor && person && (
            <>
              <div className="kiosk__step">{t('confirm')}</div>
              <h1 className="kiosk__question hi">
                {pick('क्या यह सही है?', 'Is this correct?')}
              </h1>
              <div className="kiosk__choices">
                <div className="kiosk-choice" style={{ cursor: 'default' }}>
                  <span className="kiosk-choice__sub">{pick('मरीज़', 'Patient')}</span>
                  <span className="kiosk-choice__title">{person.full_name}</span>
                </div>
                <div className="kiosk-choice" style={{ cursor: 'default' }}>
                  <span className="kiosk-choice__sub">{pick('डॉक्टर', 'Doctor')}</span>
                  <span className="kiosk-choice__title">{doctor.full_name}</span>
                </div>
                <div className="kiosk-choice" style={{ cursor: 'default' }}>
                  <span className="kiosk-choice__sub">{pick('तारीख', 'Date')}</span>
                  <span className="kiosk-choice__title">{longDate(date, lang)}</span>
                </div>
              </div>
              <div className="kiosk__actions">
                <button type="button" className="btn btn--ghost" onClick={() => setStep('date')}>
                  {t('back')}
                </button>
                <button type="button" className="btn" disabled={busy} onClick={confirm}>
                  {busy && <span className="spinner" aria-hidden />}
                  {t('confirm')}
                </button>
              </div>
            </>
          )}

          {step === 'ticket' && ticket && (
            <>
              <div className="kiosk__step">{t('booked')}</div>
              <div className="slip">
                <div className="slip__label" style={{ textAlign: 'center' }}>
                  {t('bookingReference')}
                </div>
                <div className="slip__reference">{ticket.booking_reference}</div>
                <div className="slip__grid">
                  <div>
                    <div className="slip__label">{pick('मरीज़', 'Patient')}</div>
                    <div className="slip__value">{ticket.patient_name}</div>
                  </div>
                  <div>
                    <div className="slip__label">{pick('डॉक्टर', 'Doctor')}</div>
                    <div className="slip__value">{ticket.doctor_name}</div>
                  </div>
                  <div>
                    <div className="slip__label">{t('room')}</div>
                    <div className="slip__value">{ticket.room}</div>
                  </div>
                  <div>
                    <div className="slip__label">{pick('समय', 'Time')}</div>
                    <div className="slip__value">{clock(ticket.slot_start)}</div>
                  </div>
                </div>
                <div className="slip__message hi">
                  {lang === 'hi' ? ticket.message_hi : ticket.message_en}
                </div>
              </div>
              <div className="kiosk__actions">
                <button type="button" className="btn btn--ghost" onClick={() => window.print()}>
                  {t('kioskPrint')}
                </button>
                <button type="button" className="btn" onClick={reset}>
                  {t('kioskDone')}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** On-screen numeric pad. A wall-mounted kiosk has no keyboard.
 *
 * Every update is a functional one. Building the next value from the `value`
 * prop would read a stale closure when taps land faster than React re-renders
 * — which is exactly what happens when someone double-taps a sluggish panel,
 * and silently dropping a digit from a phone number is not an acceptable
 * failure here.
 */
function Keypad({
  value,
  onChange,
  maxLength,
  placeholder,
}: {
  value: string
  onChange: (update: (previous: string) => string) => void
  maxLength: number
  placeholder: string
}) {
  const { pick } = useLang()
  const press = (digit: string) =>
    onChange((previous) => (previous.length < maxLength ? previous + digit : previous))

  return (
    <>
      <div className={`kiosk__display${value ? '' : ' kiosk__display--empty'}`}>
        {value || placeholder}
      </div>
      <div className="keypad">
        {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((digit) => (
          <button key={digit} type="button" onClick={() => press(digit)}>
            {digit}
          </button>
        ))}
        <button type="button" className="keypad--wide" onClick={() => onChange(() => '')}>
          {pick('मिटाएँ', 'Clear')}
        </button>
        <button type="button" onClick={() => press('0')}>
          0
        </button>
        <button
          type="button"
          className="keypad--wide"
          onClick={() => onChange((previous) => previous.slice(0, -1))}
        >
          ←
        </button>
      </div>
    </>
  )
}

/** One-time provisioning screen for a newly installed kiosk. */
function Setup({ onSaved }: { onSaved: () => void }) {
  const { t, pick } = useLang()
  const [value, setValue] = useState('')

  return (
    <>
      <div className="kiosk__step">{t('kioskSetup')}</div>
      <h1 className="kiosk__question">{t('kioskDeviceKey')}</h1>
      <p className="kiosk__sub">
        {pick(
          'यह कुंजी अस्पताल के प्रशासक से लें। यह एक बार ही डालनी होती है।',
          'Get this key from the hospital administrator. It is entered once per device.',
        )}
      </p>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="dev-device-key"
        style={{ fontSize: '1.4rem', minHeight: 76 }}
        autoFocus
      />
      <div className="note note--warn" style={{ marginTop: 18 }}>
        {pick(
          'यह कुंजी इस ब्राउज़र में सहेजी जाती है। कियोस्क को केवल अस्पताल के अपने उपकरण पर चलाएँ।',
          'This key is stored in the browser. Only run the kiosk on hospital-owned hardware.',
        )}
      </div>
      <div className="kiosk__actions">
        <button
          type="button"
          className="btn"
          disabled={value.trim().length < 3}
          onClick={() => {
            deviceKey.set(value)
            onSaved()
          }}
        >
          {t('kioskSave')}
        </button>
      </div>
    </>
  )
}
