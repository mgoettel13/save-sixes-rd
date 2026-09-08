import { useEffect, useState } from "react";
import "./mailing.css";

export default function MailingList({ token, setToken, Frame, API, navigate, mode }) {
  const [page, setPage] = useState({ contacts: [], total: 0 });
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState({ email: "", first_name: "", last_name: "", consent_confirmed: false, list_ids: [] });
  const [csvText, setCsvText] = useState("");
  const [defaultStatus, setDefaultStatus] = useState("unknown");
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [results, setResults] = useState([]);
  const [lists, setLists] = useState([]);
  const [listFilter, setListFilter] = useState("");
  const [listForm, setListForm] = useState({ name: "", description: "" });
  const [editingList, setEditingList] = useState(null);
  const editingContact = mode?.match(/^contacts\/(\d+)$/)?.[1];


  async function request(path, options = {}) {
    const response = await fetch(`${API}/admin/mailing-list${path}`, {
      ...options, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...options.headers },
    });
    if (response.status === 401) { localStorage.removeItem("save-sixes-admin-token"); setToken(""); }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
    }
    return response;
  }

  async function load(cursor) {
    setBusy(true); setError("");
    try {
      const params = new URLSearchParams({ search });
      if (filter) params.set("subscribed", filter);
      if (listFilter) params.set("list_id", listFilter);
      if (cursor) params.set("cursor", cursor);
      const data = await (await request(`/contacts?${params}`)).json();
      setPage(previous => ({ ...data, total: cursor ? previous.total : data.total,
        contacts: cursor ? [...previous.contacts, ...data.contacts] : data.contacts }));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    let active = true;
    request("/status").then(r => r.json()).then(data => { if (active) setConfig(data); }).catch(err => { if (active) setError(err.message); });
    request("/lists").then(r => r.json()).then(data => { if (active) setLists(data); }).catch(err => { if (active) setError(err.message); });
    if (editingContact) request(`/contacts/${editingContact}`).then(r => r.json()).then(data => { if (active) setForm(data); }).catch(err => { if (active) setError(err.message); });
    if (!mode) load();
    return () => { active = false; };
  }, [mode]);

  async function exportContacts() {
    setBusy(true); setError("");
    try {
      const blob = await (await request("/export")).blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = "save-sixes-mailing-list.csv"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function addContact(event) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await request(editingContact ? `/contacts/${editingContact}` : "/contacts", { method: editingContact ? "PATCH" : "POST", body: JSON.stringify(form) });
      setMessage(editingContact ? "Contact updated." : "Subscriber added.");
      if (!editingContact) setForm({ email: "", first_name: "", last_name: "", consent_confirmed: false, list_ids: [] });
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function unsubscribe(contact) {
    if (!window.confirm(`Unsubscribe ${contact.email} from campaign emails?`)) return;
    setBusy(true); setError("");
    try {
      await request(`/contacts/${encodeURIComponent(contact.id)}/subscription`, { method: "PATCH", body: JSON.stringify({ subscribed: false }) });
      await load(); setMessage("Contact unsubscribed from Save Sixes Rd emails.");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function previewImport(event) {
    event.preventDefault(); setBusy(true); setError(""); setResults([]);
    try { setPreview(await (await request("/import/preview", { method: "POST", body: JSON.stringify({ csv_text: csvText, default_status: defaultStatus }) })).json()); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function runImport() {
    setBusy(true); setError(""); setResults([]);
    const completed = [];
    try {
      for (let offset = 0; offset < preview.rows.length; offset += 20) {
        const data = await (await request("/import", { method: "POST", body: JSON.stringify({ rows: preview.rows.slice(offset, offset + 20), consent_confirmed: confirmed }) })).json();
        completed.push(...data.results); setResults([...completed]);
        if (data.results.some(item => item.result === "failed")) throw new Error("Import paused after a provider error. Review the results before retrying.");
      }
      setMessage("Import finished. Review the results below, then check the mailing list.");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function saveList(event) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await request(`/lists${editingList ? `/${editingList}` : ""}`, { method: editingList ? "PATCH" : "POST", body: JSON.stringify(listForm) });
      setLists(await (await request("/lists")).json()); setEditingList(null); setListForm({ name: "", description: "" }); setMessage("Mailing list saved.");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  const title = editingContact ? "Edit contact" : mode === "lists" ? "Manage lists" : mode === "import" ? "Import subscribers" : mode === "new" ? "Add a subscriber" : "Mailing list";
  return <Frame token={token} setToken={setToken} eyebrow="Community updates" title={title}
    action={mode ? <button className="text-button" disabled={busy} onClick={() => navigate("/admin/mailing-list")}>← Mailing list</button> :
      <button className="button button-primary" onClick={() => navigate("/admin/mailing-list/new")}>Add subscriber</button>}>
    {config && !config.configured && <div className="mailing-notice" role="status"><strong>Email sending needs configuration.</strong><p>Your contacts are saved here. Configure the email sender before sending campaigns.</p></div>}
    {error && <p className="admin-error" role="alert">{error}</p>}
    {message && <p className="admin-success" role="status">{message}</p>}
    {!mode && <>
      <div className="admin-summary"><p>Manage the people receiving Save Sixes Road updates. Manage names, list memberships, and subscription preferences here.</p>
        <div className="mailing-actions"><button className="text-button" onClick={() => navigate("/admin/mailing-list/lists")}>Manage lists</button><button className="text-button" onClick={() => navigate("/admin/mailing-list/import")}>Import from Squarespace</button><button className="text-button" onClick={exportContacts} disabled={busy}>Export CSV</button></div></div>
      <form className="mailing-filters" onSubmit={event => { event.preventDefault(); load(); }}>
        <label>Search by email or name<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Find a subscriber" /></label>
        <label>Subscription<select value={filter} onChange={event => setFilter(event.target.value)}><option value="">Everyone</option><option value="true">Subscribed</option><option value="false">Not subscribed</option></select></label>
        <label>Mailing list<select value={listFilter} onChange={e => setListFilter(e.target.value)}><option value="">All lists</option>{lists.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        <button className="button button-primary" disabled={busy}>{busy ? "Loading…" : "Search"}</button>
      </form>
      <div className="admin-list"><div className="admin-list-heading"><h2>{page.total} {page.total === 1 ? "contact" : "contacts"}</h2><button className="text-button" disabled={busy} onClick={() => load()}>Refresh</button></div>
        {!busy && !error && page.contacts.length === 0 && <p>No contacts found. Add a subscriber or import your Squarespace mailing list.</p>}
        <div className="mailing-table-wrap"><table className="mailing-table"><thead><tr><th>Email</th><th>Name</th><th>Status</th><th>Lists</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{page.contacts.map(contact => <tr key={contact.id}><td>{contact.email}</td><td>{[contact.first_name, contact.last_name].filter(Boolean).join(" ") || "—"}</td><td><span className={`admin-status ${contact.subscribed ? "published" : "draft"}`}>{contact.status}</span></td><td>{lists.filter(l => contact.list_ids.includes(l.id)).map(l => l.name).join(", ") || "—"}</td><td><button className="text-button" onClick={() => navigate(`/admin/mailing-list/contacts/${contact.id}`)}>Edit</button> {contact.subscribed && <button className="text-button" disabled={busy} onClick={() => unsubscribe(contact)}>Unsubscribe</button>}</td></tr>)}</tbody></table></div>
        {page.has_more && <button className="text-button" disabled={busy} onClick={() => load(page.cursor)}>Load more</button>}
      </div>
    </>}
    {(mode === "new" || editingContact) && <form className="admin-form admin-editor-page" onSubmit={addContact}>
      {!editingContact && <p>Add someone who has already asked to receive Save Sixes Rd emails. New visitors can use the website signup form to confirm their own address.</p>}
      <label>Email<input type="email" required disabled={!!editingContact} value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label>
      <label>First name<input maxLength={150} value={form.first_name} onChange={event => setForm({ ...form, first_name: event.target.value })} /></label>
      <label>Last name<input maxLength={150} value={form.last_name} onChange={event => setForm({ ...form, last_name: event.target.value })} /></label>
      <fieldset className="mailing-list-options"><legend>Mailing lists</legend>{lists.map(l => <label className="mailing-checkbox" key={l.id}><input type="checkbox" checked={form.list_ids.includes(l.id)} onChange={e => setForm({ ...form, list_ids: e.target.checked ? [...form.list_ids, l.id] : form.list_ids.filter(id => id !== l.id) })} />{l.name}</label>)}</fieldset>
      {editingContact && <p>Status: {form.status}. Source: {form.source}. {form.consent}</p>}
      {!editingContact && <label className="mailing-checkbox"><input type="checkbox" required checked={form.consent_confirmed} onChange={event => setForm({ ...form, consent_confirmed: event.target.checked })} />This person has agreed to receive our campaign emails.</label>}
      <button className="button button-primary" disabled={busy}>{busy ? "Saving…" : editingContact ? "Save contact" : "Add subscriber"}</button>
    </form>}
    {mode === "lists" && <><div className="admin-list">{lists.map(l => <article key={l.id} className="admin-post-card"><div><h2>{l.name}</h2><p>{l.description}</p><p>{l.contacts} contacts · {l.subscribed} subscribed</p></div><button className="text-button" onClick={() => { setEditingList(l.id); setListForm({ name: l.name, description: l.description }); }}>{"Edit list"}</button></article>)}</div>
      <form className="admin-form admin-editor-page" onSubmit={saveList}><h2>{editingList ? "Edit list" : "Create a list"}</h2><label>List name<input required maxLength={150} pattern="[^,]+" value={listForm.name} onChange={e => setListForm({ ...listForm, name: e.target.value })} /></label><label>Description<textarea maxLength={500} value={listForm.description} onChange={e => setListForm({ ...listForm, description: e.target.value })} /></label><button className="button button-primary" disabled={busy}>Save list</button>{editingList && <button className="text-button" type="button" onClick={() => { setEditingList(null); setListForm({ name: "", description: "" }); }}>Cancel</button>}</form></>}
    {mode === "import" && <div className="mailing-import">
      <p>Choose the CSV exported from Squarespace. Review subscription states before importing. Existing opt-outs remain opted out. Importing does not send emails.</p>
      <form className="admin-form" onSubmit={previewImport}>
        <label>Squarespace CSV<input type="file" accept=".csv,text/csv" required disabled={busy} onChange={async event => {
          const file = event.target.files?.[0]; setPreview(null); setConfirmed(false); setResults([]); setMessage(""); setCsvText("");
          if (file) { if (file.size > 5_000_000) { setError("Choose a CSV smaller than 5 MB."); return; } setCsvText(await file.text()); }
        }} /></label>
        <label>If the export has no subscription status<select value={defaultStatus} disabled={busy} onChange={event => { setDefaultStatus(event.target.value); setPreview(null); setConfirmed(false); }}>
          <option value="unknown">Leave unknown and skip these rows</option><option value="subscribed">This export contains confirmed subscribers only</option><option value="unsubscribed">This export contains unsubscribed contacts only</option>
        </select></label>
        <button className="button button-primary" disabled={busy || !csvText}>Preview import</button>
      </form>
      {preview && <section className="mailing-preview"><h2>Review import</h2><p>{preview.rows.length} unique addresses · {preview.duplicates} duplicates combined · {preview.errors.length} invalid rows</p>
        <div className="mailing-counts">{Object.entries(preview.counts).map(([status, count]) => <span key={status}><strong>{count}</strong> {status}</span>)}</div>
        {preview.errors.length > 0 && <p className="admin-error">Fix the invalid rows and upload the CSV again: {preview.errors.slice(0, 10).map(item => `row ${item.row}: ${item.message}`).join("; ")}</p>}
        <div className="mailing-table-wrap"><table className="mailing-table"><thead><tr><th>Email</th><th>Name</th><th>Imported status</th></tr></thead><tbody>{preview.rows.slice(0, 10).map(row => <tr key={row.email}><td>{row.email}</td><td>{row.first_name} {row.last_name}</td><td>{row.status}</td></tr>)}</tbody></table></div>
        <p>Showing the first {Math.min(preview.rows.length, 10)} addresses. Unknown statuses are skipped. Pending addresses remain unsubscribed.</p>
        <label className="mailing-checkbox"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} />I checked the source list and subscription states. Subscribed contacts have permission to receive Save Sixes Rd emails.</label>
        <button className="button button-primary" onClick={runImport} disabled={busy || !confirmed || !preview.rows.length || !!preview.errors.length}>{busy ? `Importing… ${results.length} processed` : "Import contacts"}</button>
      </section>}
      {results.length > 0 && <div role="status"><h2>Import results</h2><p>{results.filter(r => r.result === "created").length} created · {results.filter(r => r.result === "updated").length} updated · {results.filter(r => r.result === "skipped").length} skipped · {results.filter(r => r.result === "failed").length} failed</p>{results.filter(r => r.result === "failed").map(row => <p key={row.email}>{row.email}: {row.message}</p>)}</div>}
    </div>}
  </Frame>;
}
