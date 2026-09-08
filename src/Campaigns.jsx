import { useEffect, useState } from "react";
import "./mailing.css";

export default function Campaigns({ token, setToken, Frame, API, navigate, campaignId, create, postId }) {
  const [campaigns, setCampaigns] = useState([]);
  const [posts, setPosts] = useState([]);
  const [lists, setLists] = useState([]);
  const [form, setForm] = useState({ post_id: postId || "", subject: "", introduction: "", list_ids: [] });
  const [saved, setSaved] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [testEmail, setTestEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, { ...options, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` } });
    if (response.status === 401) { localStorage.removeItem("save-sixes-admin-token"); setToken(""); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The request could not be completed.");
    return data;
  }
  async function refresh() {
    if (campaignId) {
      const data = await request(`/admin/campaigns/${campaignId}`);
      setSaved(data);
      setForm({ post_id: data.post_id, subject: data.subject, introduction: data.introduction, list_ids: data.list_ids });
      setDirty(false);
    } else if (!create) setCampaigns(await request("/admin/campaigns"));
  }
  useEffect(() => {
    Promise.all([request("/posts"), request("/admin/mailing-list/lists")]).then(([p, l]) => {
      setPosts(p); setLists(l);
      if (create && postId) { const post = p.find(p => p.id === Number(postId)); if (post) setForm(f => ({ ...f, post_id: post.id, subject: post.title })); }
    }).catch(err => setError(err.message));
    refresh().catch(err => setError(err.message));
  }, [campaignId, create]);
  const update = (key, value) => { setForm(f => ({ ...f, [key]: value })); setDirty(true); setMessage(""); };
  async function save(event) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const data = await request(`/admin/campaigns${campaignId ? `/${campaignId}` : ""}`, { method: campaignId ? "PATCH" : "POST", body: JSON.stringify({ ...form, post_id: Number(form.post_id) }) });
      if (!campaignId) navigate(`/admin/campaigns/${data.id}`);
      else { await refresh(); setMessage("Draft saved. Review the email and recipient count below."); }
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  async function test(event) {
    event.preventDefault(); setBusy(true); setError("");
    try { const result = await request(`/admin/campaigns/${campaignId}/test`, { method: "POST", body: JSON.stringify({ email: testEmail }) }); setMessage(result.message); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  async function send() {
    if (!window.confirm(`Send “${saved.subject}” to ${saved.preview.recipient_count} subscribed people? This sends real emails and cannot be recalled.`)) return;
    setBusy(true); setError("");
    try {
      await request(`/admin/campaigns/${campaignId}/send`, { method: "POST", body: JSON.stringify({ expected_recipients: saved.preview.recipient_count, preview_token: saved.preview.preview_token }) });
      await refresh(); setMessage("Email queued. Refresh to see sending progress.");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  async function cancel() {
    if (!window.confirm("Cancel remaining queued emails? Emails already being sent may still arrive.")) return;
    setBusy(true); setError("");
    try { await request(`/admin/campaigns/${campaignId}/cancel`, { method: "POST" }); await refresh(); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  const editing = create || saved?.status === "draft";
  return <Frame token={token} setToken={setToken} eyebrow="Share the news" title={create ? "New blog email" : campaignId ? "Blog email" : "Email campaigns"}
    action={<button className="button button-primary" onClick={() => navigate(campaignId || create ? "/admin/campaigns" : "/admin/campaigns/new")}>{campaignId || create ? "← All emails" : "New email"}</button>}>
    {error && <p role="alert" className="admin-error">{error}</p>}{message && <p role="status" className="admin-success">{message}</p>}
    {!campaignId && !create && <><p>Share a published blog post with all subscribers or selected mailing lists. Preview and send a test before sending to your audience.</p><div className="admin-list">
      <div className="admin-list-heading"><h2>{campaigns.length} emails</h2><button className="text-button" onClick={() => { setMessage(""); refresh().catch(e => setError(e.message)); }}>Refresh</button></div>
      {!campaigns.length && <p>Your email drafts and sending history will appear here.</p>}
      {campaigns.map(c => <article className="admin-post-card" key={c.id}><div><span className="admin-status">{c.status}</span><h3>{c.subject}</h3><p>{new Date(c.created_at).toLocaleDateString()} · {Object.entries(c.counts).map(([s, n]) => `${n} ${s}`).join(" · ") || "Draft"}</p></div><button className="text-button" onClick={() => navigate(`/admin/campaigns/${c.id}`)}>Open email →</button></article>)}
    </div></>}
    {editing && <form className="admin-form admin-editor-page" onSubmit={save}>
      <label>Published blog post<select required value={form.post_id} onChange={e => { update("post_id", e.target.value); if (!form.subject) update("subject", posts.find(p => p.id === Number(e.target.value))?.title || ""); }}><option value="">Choose a post</option>{posts.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
      <label>Email subject<input required maxLength={200} value={form.subject} onChange={e => update("subject", e.target.value)} /></label>
      <label>Opening note (optional)<textarea rows={4} maxLength={10000} value={form.introduction} onChange={e => update("introduction", e.target.value)} /></label>
      <fieldset className="mailing-list-options"><legend>Recipients</legend><p>{form.list_ids.length ? "Subscribed people in any selected list. Each person receives one email." : "All subscribed people, including those without a list."}</p>
        <label className="mailing-checkbox"><input type="checkbox" checked={!form.list_ids.length} onChange={() => update("list_ids", [])} />All subscribers</label>
        {lists.map(l => <label className="mailing-checkbox" key={l.id}><input type="checkbox" checked={form.list_ids.includes(l.id)} onChange={e => update("list_ids", e.target.checked ? [...form.list_ids, l.id] : form.list_ids.filter(id => id !== l.id))} />{l.name} ({l.subscribed} subscribed)</label>)}
      </fieldset><button className="button button-primary" disabled={busy}>{busy ? "Saving…" : "Save and preview"}</button>
    </form>}
    {saved && <section className="campaign-preview"><div className="admin-list-heading"><div><h2>{saved.status === "draft" ? "Review before sending" : saved.subject}</h2><p>{saved.preview?.recipient_count} {saved.preview?.recipient_count === 1 ? "recipient" : "recipients"} · {saved.status}</p></div><button className="text-button" disabled={busy || dirty} onClick={() => { setMessage(""); refresh().catch(e => setError(e.message)); }}>Refresh</button></div>
      {dirty && <p className="mailing-notice">Save your changes to update this preview before testing or sending.</p>}
      <iframe title="Email preview" sandbox="" referrerPolicy="no-referrer" srcDoc={saved.preview?.html || ""} />
      <form className="mailing-filters" onSubmit={test}><label>Test recipient<input type="email" required value={testEmail} onChange={e => setTestEmail(e.target.value)} placeholder="Your email address" /></label><button className="button button-primary" disabled={busy || dirty}>Send test email</button></form>
      {saved.status === "draft" && <button className="button button-primary" disabled={busy || dirty || !saved.preview?.recipient_count} onClick={send}>Send to {saved.preview?.recipient_count} {saved.preview?.recipient_count === 1 ? "subscriber" : "subscribers"}</button>}
      {["queued", "sending"].includes(saved.status) && <button className="text-button" disabled={busy} onClick={cancel}>Cancel remaining emails</button>}
      {saved.status !== "draft" && <><p>Accepted means Plunk accepted the message; it does not confirm inbox delivery. Failed or uncertain sends need review in Plunk and are not resent automatically.</p><div className="mailing-counts">{Object.entries(saved.counts).map(([s, n]) => <span key={s}><strong>{n}</strong>{s}</span>)}</div><div className="mailing-table-wrap"><table className="mailing-table"><thead><tr><th>Recipient</th><th>Status</th><th>Details</th></tr></thead><tbody>{saved.deliveries.map(d => <tr key={d.email}><td>{d.email}</td><td>{d.status}</td><td>{d.error || "—"}</td></tr>)}</tbody></table></div></>}
    </section>}
  </Frame>;
}

export function SubscriptionAction({ API, action }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("token") || "");
  async function submit() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${API}/newsletter/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "This link is invalid. Please use the link in your email.");
      setMessage(data.message); window.history.replaceState({}, "", window.location.pathname);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return <main className="section-wrap subscription-action"><p className="eyebrow">Save Sixes Rd email updates</p><h1>{action === "confirm" ? "Confirm your subscription" : "Unsubscribe from updates"}</h1>
    {message ? <p role="status">{message}</p> : <><p>{action === "confirm" ? "Receive campaign news, meeting updates, and ways to get involved. You can unsubscribe at any time." : "Use the button below to stop receiving Save Sixes Rd emails."}</p><button className="button button-primary" disabled={busy || !token} onClick={submit}>{busy ? "Saving…" : action === "confirm" ? "Confirm my subscription" : "Unsubscribe"}</button>{!token && <p>Please open the full link from your email.</p>}</>}
    {error && <p role="alert">{error}</p>}<p><a href="/">Return to the website</a></p>
  </main>;
}
