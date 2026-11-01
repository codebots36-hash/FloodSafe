import { useSyncExternalStore } from 'react'

/**
 * App-wide settings: language, text size and the replay date. Kept in
 * localStorage so they survive a reload, and shared through one tiny store so
 * every screen sees a change at once.
 */
export type Lang = 'en' | 'hi'
export type TextSize = 'small' | 'normal' | 'large'

export interface Settings {
  lang: Lang
  textSize: TextSize
  /** A past date (YYYY-MM-DD) to replay, or null for live data. */
  replay: string | null
}

const KEY = 'floodsafe-settings'
const DEFAULTS: Settings = { lang: 'en', textSize: 'normal', replay: null }

function load(): Settings {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') }
  } catch {
    return DEFAULTS
  }
}

let current = load()
const listeners = new Set<() => void>()

export function setSetting<K extends keyof Settings>(key: K, value: Settings[K]) {
  current = { ...current, [key]: value }
  try {
    localStorage.setItem(KEY, JSON.stringify(current))
  } catch {
    // Remembering settings is optional.
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useSettings() {
  return useSyncExternalStore(subscribe, () => current)
}

export const TEXT_ZOOM: Record<TextSize, number> = { small: 0.9, normal: 1, large: 1.2 }

/**
 * Hindi for the app's own words, keyed by the English. Data from the API
 * (place names, official alert text, the API's notes) is shown as it comes.
 */
const HI: Record<string, string> = {
  'Text size:': 'अक्षर का आकार:',
  'Flood-Aware Road Advisory — Uttarakhand': 'बाढ़-सजग सड़क सलाह — उत्तराखंड',
  'FloodSafe Portal — Flood-Aware Road Advisory Service': 'फ्लडसेफ पोर्टल — बाढ़-सजग सड़क सलाह सेवा',
  'System Status': 'सिस्टम स्थिति',
  'Flood Guidance': 'बाढ़ मार्गदर्शन',
  Services: 'सेवाएँ',
  'Hazard Reports': 'खतरे की रिपोर्ट',
  'Open Map Tool': 'मानचित्र खोलें',
  'Public Advisory:': 'जन परामर्श:',
  'Road conditions can change rapidly during monsoon season. Always verify local conditions before travel.':
    'मानसून में सड़क की स्थिति तेज़ी से बदल सकती है। यात्रा से पहले स्थानीय स्थिति ज़रूर जाँचें।',
  "Fastest isn't always": 'सबसे तेज़ रास्ता हमेशा',
  'safe.': 'सुरक्षित नहीं।',
  'FloodSafe advises road routes across Uttarakhand using a georeferenced flash-flood hazard classification, live rainfall data, and citizen-reported hazards — instead of distance alone.':
    'फ्लडसेफ केवल दूरी नहीं, बल्कि बाढ़-खतरे के मानचित्र, ताज़ा वर्षा और नागरिकों की रिपोर्ट के आधार पर उत्तराखंड में सड़क मार्ग सुझाता है।',
  'Open the Map Tool →': 'मानचित्र खोलें →',
  'View System Status': 'सिस्टम स्थिति देखें',
  'In danger? Text your family your location': 'खतरे में हैं? परिवार को अपना स्थान भेजें',
  'Hold for 2 seconds to send': 'भेजने के लिए 2 सेकंड दबाए रखें',
  'Your name': 'आपका नाम',
  'Phone numbers to text (comma separated, with country code)': 'जिन्हें संदेश भेजना है उनके फ़ोन नंबर (कॉमा से अलग, देश कोड सहित)',
  'Also show my location to responders for 24 hours': '24 घंटे तक बचाव दल को भी मेरा स्थान दिखाएँ',
  'Send my location now': 'अभी मेरा स्थान भेजें',
  'Getting your location…': 'आपका स्थान लिया जा रहा है…',
  'SYSTEM STATUS': 'सिस्टम स्थिति',
  Metric: 'मापदंड',
  Value: 'मान',
  'Road network nodes covered': 'सड़क नेटवर्क के बिंदु',
  'Shelters & hospitals mapped': 'आश्रय और अस्पताल',
  'Active hazard reports right now': 'अभी सक्रिय खतरा रिपोर्ट',
  'Wettest zone, last 24 h': 'सबसे अधिक वर्षा वाला क्षेत्र, पिछले 24 घंटे',
  'Zones at WATCH or CRITICAL': 'WATCH या CRITICAL क्षेत्र',
  'Waiting for live data': 'ताज़ा डेटा की प्रतीक्षा',
  'Flood-aware map tool': 'बाढ़-सजग मानचित्र',
  'Road and area hazard overview': 'सड़क और क्षेत्र के खतरे',
  'Route planning, hazard layers, shelters, hospitals and community reports open in a full-screen map in a new tab.':
    'रास्ता योजना, खतरे की परतें, आश्रय, अस्पताल और सामुदायिक रिपोर्ट नए टैब में पूरे मानचित्र पर खुलती हैं।',
  'Open the full-screen map →': 'पूरा मानचित्र खोलें →',
  'Use official guidance during emergencies.': 'आपात स्थिति में आधिकारिक निर्देशों का पालन करें।',
  'FLOOD GUIDANCE': 'बाढ़ मार्गदर्शन',
  Zone: 'क्षेत्र',
  'Choose a zone': 'क्षेत्र चुनें',
  'Rain so far': 'अब तक की वर्षा',
  'Rain in the 48 h before that': 'उससे पहले के 48 घंटे की वर्षा',
  'WATCH at': 'WATCH सीमा',
  'CRITICAL at': 'CRITICAL सीमा',
  'Soil group': 'मिट्टी समूह',
  Catchment: 'जलग्रहण क्षेत्र',
  'Hazard class': 'खतरा श्रेणी',
  'Stay aware': 'सतर्क रहें',
  'Be prepared': 'तैयार रहें',
  'Take immediate precautions': 'तुरंत सावधानी बरतें',
  'Before you travel': 'यात्रा से पहले',
  'General safety guidance. Follow official local instructions during emergencies.':
    'सामान्य सुरक्षा सलाह। आपात स्थिति में स्थानीय आधिकारिक निर्देशों का पालन करें।',
  SAFETY: 'सुरक्षा',
  'Official warnings': 'आधिकारिक चेतावनियाँ',
  'From NDMA SACHET, as issued by IMD, CWC and the state disaster authority.':
    'NDMA सचेत से, जैसी IMD, CWC और राज्य आपदा प्राधिकरण ने जारी कीं।',
  'No current official warnings for Uttarakhand.': 'उत्तराखंड के लिए अभी कोई आधिकारिक चेतावनी नहीं है।',
  'The official warning feed could not be read just now, so warnings may be missing. Check sachet.ndma.gov.in.':
    'आधिकारिक चेतावनी फ़ीड अभी नहीं पढ़ी जा सकी, इसलिए चेतावनियाँ छूट सकती हैं। sachet.ndma.gov.in देखें।',
  'Checking official warnings…': 'आधिकारिक चेतावनियाँ जाँची जा रही हैं…',
  Until: 'तक',
  'EMERGENCY RESPONSE': 'आपात प्रतिक्रिया',
  'Responder dashboard': 'बचाव दल डैशबोर्ड',
  'Community reports (24 h)': 'सामुदायिक रिपोर्ट (24 घंटे)',
  'Zones not SAFE': 'असुरक्षित क्षेत्र',
  'Shelters listed': 'सूचीबद्ध आश्रय',
  'SOS shared (24 h)': 'साझा SOS (24 घंटे)',
  'ALERT PREFERENCES': 'अलर्ट विकल्प',
  'Push notifications': 'पुश सूचनाएँ',
  'Get an alert on this device when the zone you choose turns CRITICAL.':
    'चुना गया क्षेत्र CRITICAL होने पर इस डिवाइस पर अलर्ट पाएँ।',
  'Turn on alerts': 'अलर्ट चालू करें',
  'HISTORICAL REVIEW': 'ऐतिहासिक समीक्षा',
  'Replay mode': 'रीप्ले मोड',
  'Run routing, zones and guidance on the rain that actually fell on a past day.':
    'किसी पिछले दिन हुई असली वर्षा पर रास्ते, क्षेत्र और मार्गदर्शन चलाएँ।',
  'Replay date': 'रीप्ले तिथि',
  'Start replay': 'रीप्ले शुरू करें',
  'Back to live': 'लाइव पर लौटें',
  'Replay of': 'रीप्ले:',
  Leave: 'प्रस्थान',
  now: 'अभी',
  Offline: 'ऑफ़लाइन',
  'Safest': 'सबसे सुरक्षित',
  'Fastest': 'सबसे तेज़',
  'Solid line': 'मोटी रेखा',
  'Thin line underneath': 'नीचे पतली रेखा',
  'Distance': 'दूरी',
  'Driving time': 'यात्रा समय',
  'Wettest hour on the way': 'रास्ते का सबसे बरसाती घंटा',
  'Kilometres by risk class': 'खतरा श्रेणी अनुसार किलोमीटर',
  'Compare flood-aware routes': 'बाढ़-सजग रास्तों की तुलना',
  'From': 'कहाँ से',
  'To': 'कहाँ तक',
  'Select a place…': 'स्थान चुनें…',
  'Get routes': 'रास्ते देखें',
  'Finding routes…': 'रास्ते खोजे जा रहे हैं…',
  'Could not get a route.': 'रास्ता नहीं मिल सका।',
  'Leave on': 'प्रस्थान',
  'Worst hour': 'सबसे भारी वर्षा का घंटा',
  'Now': 'अभी',
  'Pick two places, or click the map to set': 'दो जगहें चुनें, या मानचित्र पर क्लिक करके सेट करें',
  'Both the safest and the fastest route are fetched.': 'सबसे सुरक्षित और सबसे तेज़, दोनों रास्ते लाए जाते हैं।',
  'Fetching the safest and fastest routes — this can take a few seconds.':
    'सबसे सुरक्षित और सबसे तेज़ रास्ते लाए जा रहे हैं — इसमें कुछ सेकंड लग सकते हैं।',
  'Each stretch is judged on the rain at the hour you reach it (India time).': 'हर हिस्से को उस घंटे की वर्षा से आँका जाता है जब आप वहाँ पहुँचेंगे (भारतीय समय)।',
  'Search a place, e.g. Rishikesh': 'स्थान खोजें, जैसे ऋषिकेश',
  'Flood hazard area': 'बाढ़ खतरा क्षेत्र',
  'Road risk': 'सड़क का खतरा',
  'Shelters': 'आश्रय',
  'Hospitals': 'अस्पताल',
  'Community reports': 'सामुदायिक रिपोर्ट',
  'Report a problem': 'समस्या बताएँ',
  'Cancel': 'रद्द करें',
  'Full screen': 'पूरी स्क्रीन',
  'Exit full screen': 'पूरी स्क्रीन बंद करें',
  'Click the map where the problem is': 'जहाँ समस्या है, मानचित्र पर वहाँ क्लिक करें',
  'Loading risk layer…': 'खतरे की परत लोड हो रही है…',
  'Legend': 'संकेत',
  'Recent reports from the community': 'लोगों की हाल की रिपोर्ट',
  'Problem type': 'समस्या का प्रकार',
  'Flooded': 'पानी भरा',
  'Blocked': 'रास्ता बंद',
  'Landslide': 'भूस्खलन',
  'Note (optional)': 'टिप्पणी (वैकल्पिक)',
  'Submit report': 'रिपोर्ट भेजें',
  'No community reports available.': 'कोई सामुदायिक रिपोर्ट नहीं है।',
  'Confirm': 'पुष्टि करें',
  'Confirming…': 'पुष्टि हो रही है…',
  'Rain at selected point': 'चुने गए स्थान पर वर्षा',
  'Close': 'बंद करें',
  'Current rain': 'अभी की वर्षा',
  'Last 72 hours': 'पिछले 72 घंटे',
  'Next 24 hours': 'अगले 24 घंटे',
  'Watched zones': 'निगरानी वाले क्षेत्र',
  'Name, district and current status': 'नाम, ज़िला और अभी की स्थिति',
  'Search zones': 'क्षेत्र खोजें',
  'Search by zone or district': 'क्षेत्र या ज़िले से खोजें',
  'Rain here is below the level that has come before floods. Keep checking updates.': 'यहाँ वर्षा उस स्तर से कम है जिस पर पहले बाढ़ आई है। जानकारी देखते रहें।',
  'Rain here has reached the WATCH level. Plan ahead.': 'यहाँ वर्षा WATCH स्तर तक पहुँच गई है। पहले से योजना बनाएँ।',
  'Rain here has reached the CRITICAL level. Follow instructions from local authorities.': 'यहाँ वर्षा CRITICAL स्तर तक पहुँच गई है। स्थानीय प्रशासन के निर्देश मानें।',
  'Choose a zone to see its rain against its own WATCH and CRITICAL levels.': 'किसी क्षेत्र की वर्षा उसके अपने WATCH और CRITICAL स्तरों से तुलना देखने के लिए क्षेत्र चुनें।',
  'Check official weather and local alerts before travelling.': 'यात्रा से पहले आधिकारिक मौसम और स्थानीय अलर्ट देखें।',
  'Avoid crossing flooded roads or streams.': 'पानी भरी सड़कें या नाले पार न करें।',
  'Avoid low-lying roads and areas near rivers.': 'निचली सड़कों और नदियों के पास के इलाकों से बचें।',
  'Keep your phone charged and monitor official alerts.': 'फ़ोन चार्ज रखें और आधिकारिक अलर्ट देखते रहें।',
  'Consider delaying non-essential travel.': 'ग़ैर-ज़रूरी यात्रा टालने पर विचार करें।',
  'Move away from flooded areas and rising water.': 'बाढ़ वाले इलाकों और बढ़ते पानी से दूर जाएँ।',
  'Move to higher ground if advised by authorities.': 'प्रशासन कहे तो ऊँचे स्थान पर जाएँ।',
  'Call local emergency services if you are in immediate danger.': 'तुरंत खतरे में हों तो स्थानीय आपात सेवा (112) को फ़ोन करें।',
  'Avoid low-lying roads and areas near rivers during heavy rain.': 'भारी वर्षा में निचली सड़कों और नदियों के पास के इलाकों से बचें।',
  'Keep your phone charged during monsoon travel.': 'मानसून यात्रा में फ़ोन चार्ज रखें।',
  'rain that actually fell that day, not live.': 'उस दिन असल में हुई वर्षा, लाइव नहीं।',
}

export function translate(lang: Lang, text: string) {
  return lang === 'hi' ? (HI[text] ?? text) : text
}

/** t('English text') in the current language. */
export function useT() {
  const { lang } = useSettings()
  return (text: string) => translate(lang, text)
}
