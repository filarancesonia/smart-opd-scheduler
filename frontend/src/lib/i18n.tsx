/* Hindi/English UI strings.
 *
 * The backend already returns bilingual text for anything a patient reads
 * aloud or off a screen — board status lines, queue messages, kiosk slips —
 * so this covers the interface chrome only. Hindi is the default because the
 * people this system is for read Hindi first.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type Lang = 'hi' | 'en'

const STORAGE_KEY = 'opd.lang'

const strings = {
  // chrome
  appName: { hi: 'स्मार्ट ओपीडी', en: 'Smart OPD' },
  tagline: {
    hi: 'डॉक्टर कब हैं, यह अब अनुमान नहीं है',
    en: 'When the doctor is in is no longer a guess',
  },
  language: { hi: 'English', en: 'हिन्दी' },
  signIn: { hi: 'लॉग इन करें', en: 'Sign in' },
  signOut: { hi: 'लॉग आउट', en: 'Sign out' },
  register: { hi: 'नया खाता बनाएँ', en: 'Create account' },
  loading: { hi: 'लोड हो रहा है…', en: 'Loading…' },
  retry: { hi: 'दोबारा कोशिश करें', en: 'Try again' },
  cancel: { hi: 'रद्द करें', en: 'Cancel' },
  confirm: { hi: 'पुष्टि करें', en: 'Confirm' },
  back: { hi: 'पीछे', en: 'Back' },
  next: { hi: 'आगे', en: 'Next' },
  close: { hi: 'बंद करें', en: 'Close' },
  refresh: { hi: 'ताज़ा करें', en: 'Refresh' },

  // nav
  navBook: { hi: 'अपॉइंटमेंट', en: 'Appointments' },
  navQueue: { hi: 'मेरी बारी', en: 'My turn' },
  navDoctor: { hi: 'डॉक्टर पैनल', en: 'Doctor console' },
  navAdmin: { hi: 'प्रशासन', en: 'Administration' },

  // auth
  phone: { hi: 'मोबाइल नंबर', en: 'Mobile number' },
  password: { hi: 'पासवर्ड', en: 'Password' },
  fullName: { hi: 'पूरा नाम', en: 'Full name' },
  phoneHint: { hi: '10 अंकों का मोबाइल नंबर', en: '10-digit mobile number' },
  passwordHint: { hi: 'कम से कम 8 अक्षर', en: 'At least 8 characters' },
  noAccount: { hi: 'खाता नहीं है?', en: 'No account?' },
  haveAccount: { hi: 'पहले से खाता है?', en: 'Already have an account?' },

  // booking
  chooseDepartment: { hi: 'विभाग चुनें', en: 'Choose a department' },
  chooseDoctor: { hi: 'डॉक्टर चुनें', en: 'Choose a doctor' },
  chooseDate: { hi: 'तारीख चुनें', en: 'Choose a date' },
  chooseSlot: { hi: 'समय चुनें', en: 'Choose a time' },
  book: { hi: 'बुक करें', en: 'Book' },
  booked: { hi: 'बुक हो गया', en: 'Booked' },
  bookingReference: { hi: 'बुकिंग नंबर', en: 'Booking number' },
  room: { hi: 'कमरा', en: 'Room' },
  noSlots: { hi: 'इस दिन कोई समय उपलब्ध नहीं है', en: 'No times available on this day' },
  slotsLeft: { hi: 'समय बचे हैं', en: 'times left' },
  onLeave: { hi: 'अवकाश पर', en: 'On leave' },
  myAppointments: { hi: 'मेरे अपॉइंटमेंट', en: 'My appointments' },
  noAppointments: { hi: 'अभी कोई अपॉइंटमेंट नहीं है', en: 'No appointments yet' },
  today: { hi: 'आज', en: 'Today' },
  tomorrow: { hi: 'कल', en: 'Tomorrow' },

  // presence
  present: { hi: 'उपलब्ध', en: 'Available' },
  absent: { hi: 'अभी नहीं पहुँचे', en: 'Not arrived' },
  onLeaveToday: { hi: 'आज अवकाश पर', en: 'On leave today' },
  since: { hi: 'से', en: 'since' },
  minutesLate: { hi: 'मिनट देरी', en: 'minutes late' },

  // queue
  yourToken: { hi: 'आपका टोकन', en: 'Your token' },
  peopleAhead: { hi: 'लोग आगे', en: 'people ahead' },
  nowServing: { hi: 'अभी बुलाया जा रहा है', en: 'Now serving' },
  waiting: { hi: 'प्रतीक्षा में', en: 'Waiting' },
  notInQueue: { hi: 'आप अभी किसी कतार में नहीं हैं', en: 'You are not in a queue' },
  notInQueueHint: {
    hi: 'रिसेप्शन पर अपना बुकिंग नंबर दिखाकर टोकन लें।',
    en: 'Show your booking number at reception to get a token.',
  },

  // doctor console
  callNext: { hi: 'अगला बुलाएँ', en: 'Call next' },
  startConsult: { hi: 'जाँच शुरू करें', en: 'Start consultation' },
  completeConsult: { hi: 'पूरा करें', en: 'Complete' },
  skip: { hi: 'छोड़ें', en: 'Skip' },
  noShow: { hi: 'अनुपस्थित', en: 'No show' },
  openQueue: { hi: 'कतार शुरू करें', en: 'Open queue' },
  markArrived: { hi: 'मैं पहुँच गया/गई', en: 'Mark me arrived' },
  queueClosed: { hi: 'कतार अभी शुरू नहीं हुई', en: 'Queue not open yet' },
  emptyQueue: { hi: 'कतार में कोई नहीं है', en: 'Nobody in the queue' },

  // admin
  doctorsPresent: { hi: 'उपस्थित डॉक्टर', en: 'Doctors present' },
  absentRostered: { hi: 'ड्यूटी पर अनुपस्थित', en: 'Absent while rostered' },
  patientsWaiting: { hi: 'प्रतीक्षारत मरीज़', en: 'Patients waiting' },
  longestWait: { hi: 'सबसे लंबी प्रतीक्षा', en: 'Longest wait' },
  emergencies: { hi: 'आपात मामले', en: 'Active emergencies' },
  attendance: { hi: 'उपस्थिति', en: 'Attendance' },
  waitTimes: { hi: 'प्रतीक्षा समय', en: 'Wait times' },
  channels: { hi: 'बुकिंग माध्यम', en: 'Booking channels' },
  alerts: { hi: 'चेतावनियाँ', en: 'Alerts' },
  noAlerts: { hi: 'कोई चेतावनी नहीं', en: 'Nothing needs attention' },

  // kiosk
  kioskWelcome: { hi: 'नमस्ते', en: 'Welcome' },
  kioskStart: { hi: 'अपॉइंटमेंट लें', en: 'Book an appointment' },
  kioskEnterPhone: { hi: 'अपना मोबाइल नंबर डालें', en: 'Enter your mobile number' },
  kioskWhoIsThis: { hi: 'यह किसके लिए है?', en: 'Who is this for?' },
  kioskNewPerson: { hi: 'नया नाम जोड़ें', en: 'Add a new person' },
  kioskYourName: { hi: 'आपका नाम', en: 'Your name' },
  kioskYourAge: { hi: 'आपकी उम्र', en: 'Your age' },
  kioskPrint: { hi: 'पर्ची प्रिंट करें', en: 'Print slip' },
  kioskDone: { hi: 'हो गया', en: 'Done' },
  kioskSetup: { hi: 'कियोस्क सेटअप', en: 'Kiosk setup' },
  kioskDeviceKey: { hi: 'डिवाइस कुंजी', en: 'Device key' },
  kioskSave: { hi: 'सहेजें', en: 'Save' },

  // minutes
  minutes: { hi: 'मिनट', en: 'minutes' },
  minutesShort: { hi: 'मि', en: 'min' },
} as const

export type StringKey = keyof typeof strings

type Ctx = {
  lang: Lang
  setLang: (lang: Lang) => void
  toggle: () => void
  t: (key: StringKey) => string
  /** Pick the matching half of a bilingual pair returned by the backend. */
  pick: (hi: string, en: string) => string
}

const LanguageContext = createContext<Ctx | null>(null)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(
    () => (localStorage.getItem(STORAGE_KEY) as Lang) || 'hi',
  )

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, lang)
    document.documentElement.lang = lang
  }, [lang])

  const setLang = useCallback((next: Lang) => setLangState(next), [])
  const toggle = useCallback(() => setLangState((l) => (l === 'hi' ? 'en' : 'hi')), [])

  const value = useMemo<Ctx>(
    () => ({
      lang,
      setLang,
      toggle,
      t: (key) => strings[key][lang],
      pick: (hi, en) => (lang === 'hi' ? hi : en),
    }),
    [lang, setLang, toggle],
  )

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>
}

export function useLang(): Ctx {
  const ctx = useContext(LanguageContext)
  if (!ctx) throw new Error('useLang must be used inside LanguageProvider')
  return ctx
}
