/**
 * Filters as a GET form.
 *
 * No client state: submitting changes the URL, the server re-reads it, and the
 * filtered table is a link someone can share or return to. Every control has a
 * real `<label>` (not a placeholder), the submit button is a real button, and
 * the form works with JavaScript disabled — which is the accessibility floor,
 * not a bonus.
 */
export function FilterBar({
  action,
  children,
  resultNote,
}: {
  action: string;
  children: React.ReactNode;
  /** Shown next to the button when a filter only covers the loaded page. */
  resultNote?: string;
}) {
  return (
    <form className="toolbar filter-bar" action={action} method="get" role="search">
      {children}
      <span className="spacer" />
      {resultNote ? (
        <span className="filter-note" id="filter-note">
          {resultNote}
        </span>
      ) : null}
      <button type="submit" className="button button-primary">
        Apply
      </button>
      <a className="button button-ghost" href={action}>
        Reset
      </a>
    </form>
  );
}

export function TextFilter({
  name,
  label,
  defaultValue,
  placeholder,
  hint,
}: {
  name: string;
  label: string;
  defaultValue?: string;
  placeholder?: string;
  hint?: string;
}) {
  const id = `filter-${name}`;
  return (
    <span className="field-inline">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        className="input"
        id={id}
        name={name}
        type="search"
        defaultValue={defaultValue ?? ""}
        placeholder={placeholder}
        aria-describedby={hint ? `${id}-hint` : undefined}
      />
      {hint ? (
        <span className="field-hint" id={`${id}-hint`}>
          {hint}
        </span>
      ) : null}
    </span>
  );
}

export function SelectFilter({
  name,
  label,
  options,
  defaultValue,
}: {
  name: string;
  label: string;
  options: { value: string; label: string }[];
  defaultValue?: string;
}) {
  const id = `filter-${name}`;
  return (
    <span className="field-inline">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <select className="input" id={id} name={name} defaultValue={defaultValue ?? ""}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </span>
  );
}

/** Hidden inputs that carry filters the form does not render, e.g. `limit`. */
export function CarryFilters({ filters, except }: { filters: Record<string, string | undefined>; except: string[] }) {
  return (
    <>
      {Object.entries(filters)
        .filter(([key, value]) => !except.includes(key) && value !== undefined && value !== "")
        .map(([key, value]) => (
          <input key={key} type="hidden" name={key} value={value} />
        ))}
    </>
  );
}