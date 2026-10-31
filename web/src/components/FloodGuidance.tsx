type FloodStatus = 'SAFE' | 'WATCH' | 'CRITICAL'

type FloodGuidanceProps = {
  /** The area's live status. Leave it out until a status feed is connected. */
  status?: FloodStatus
}

const guidance = {
  SAFE: {
    title: 'Stay aware',
    message: 'Conditions are currently within normal limits. Continue checking updates.',
    actions: [
      'Check official weather and local alerts before travelling.',
      'Avoid crossing flooded roads or streams.',
    ],
  },
  WATCH: {
    title: 'Be prepared',
    message: 'Rainfall or local conditions may increase flood risk. Plan ahead.',
    actions: [
      'Avoid low-lying roads and areas near rivers.',
      'Keep your phone charged and monitor official alerts.',
      'Consider delaying non-essential travel.',
    ],
  },
  CRITICAL: {
    title: 'Take immediate precautions',
    message: 'Flood risk is high. Follow instructions from local authorities.',
    actions: [
      'Move away from flooded areas and rising water.',
      'Move to higher ground if advised by authorities.',
      'Call local emergency services if you are in immediate danger.',
    ],
  },
}

// Shown when no status is known: advice that holds in any conditions, with
// no claim about what the conditions are.
const generalGuidance = {
  title: 'Before you travel',
  message:
    'The current flood status for your area is not connected here yet. Check official alerts for your route.',
  actions: [
    'Check official weather and local alerts before travelling.',
    'Avoid crossing flooded roads or streams.',
    'Avoid low-lying roads and areas near rivers during heavy rain.',
    'Keep your phone charged during monsoon travel.',
  ],
}

export default function FloodGuidance({ status }: FloodGuidanceProps) {
  const advice = status ? guidance[status] : generalGuidance
  const variant = status ? status.toLowerCase() : 'general'

  return (
    <section className={`guidance-card guidance-${variant}`}>
      <p className="eyebrow">FLOOD GUIDANCE</p>
      <h3>{advice.title}</h3>
      <p>{advice.message}</p>

      <ul>
        {advice.actions.map((action) => (
          <li key={action}>{action}</li>
        ))}
      </ul>

      <p className="guidance-note">
        General safety guidance. Follow official local instructions during emergencies.
      </p>
    </section>
  )
}
