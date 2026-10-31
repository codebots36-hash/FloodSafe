/** Marks a screen whose content is a design preview, not data from the API. */
export default function PreviewBadge() {
  return (
    <span className="preview-badge" role="note">
      Preview — sample content, not live data
    </span>
  )
}
