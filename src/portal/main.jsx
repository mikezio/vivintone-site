import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { beginSignIn, clearSession, finishSignIn, getStoredSession, loadAuthConfig, refreshSession, resendSignInCode, sessionMinutes } from './auth';
import { portalRequest, requestList } from './api';
import './styles.css';

const labels = {
  submitted: 'Submitted', reviewing: 'In review', on_hold: 'On hold', info_requested: 'Information needed',
  approved: 'Approved to ship', declined: 'Not accepted', received: 'Received', reimbursed: 'Reimbursed', closed: 'Closed',
  pre_transit: 'Label created', registered: 'Tracking registered', in_transit: 'In transit', out_for_delivery: 'Out for delivery', delivered: 'Delivered',
  awaiting_receipt: 'Not requested', not_requested: 'Waived', submitted_reimbursement: 'Submitted', paid: 'Paid',
  approve: 'Approved to ship', decline: 'Request declined', hold: 'Request placed on hold', request_info: 'Information requested',
  updated: 'Request updated', tracking_submitted: 'Tracking registered', prepaid_label_requested: 'Prepaid label requested',
  prepaid_label_purchased: 'Prepaid label created', prepaid_label_refunded: 'Prepaid label refunded', reimbursement_not_requested: 'Reimbursement waived',
  reimbursement_submitted: 'Reimbursement submitted', reimbursed: 'Reimbursement paid',
};
const display = value => labels[value] || String(value || 'Not started').replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase());
const date = value => value ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '';

function App() {
  const [config, setConfig] = useState(null);
  const [session, setSession] = useState(getStoredSession);
  const [fatal, setFatal] = useState('');
  useEffect(() => { loadAuthConfig().then(setConfig).catch(error => setFatal(error.message)); }, []);
  useEffect(() => {
    if (!session || !config) return;
    const delay = Math.max(1000, session.expiresAt - Date.now() - 60000);
    const timer = setTimeout(async () => setSession(await refreshSession(config, session)), delay);
    return () => clearTimeout(timer);
  }, [config, session]);
  const logout = message => { clearSession(); setSession(null); if (message) setFatal(message); };
  if (!config) return <Shell><Centered>{fatal ? <Notice type="error">{fatal}</Notice> : <Loading label="Preparing secure sign-in…" />}</Centered></Shell>;
  return <Shell session={session} logout={() => logout()}>{session
    ? <Dashboard session={session} config={config} onSession={setSession} onExpired={() => logout('Your secure session ended. Sign in again to continue.')} />
    : <SignIn config={config} onAuthenticated={value => { setFatal(''); setSession(value); }} message={fatal} />}
  </Shell>;
}

function SignIn({ config, onAuthenticated, message }) {
  const [identifier, setIdentifier] = useState('');
  const [attempt, setAttempt] = useState(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const codeRef = useRef(null);
  const send = async event => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      if (!config.contributorSmsEnabled && !identifier.trim().includes('@')) throw new Error('Use the verified email address from your request.');
      const value = await beginSignIn(config, identifier); value.authenticated ? onAuthenticated(value.authenticated) : (setAttempt(value), setCode(''), setTimeout(() => codeRef.current?.focus(), 0));
    }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const verify = async event => {
    event.preventDefault(); setBusy(true); setError('');
    try { onAuthenticated(await finishSignIn(config, attempt, code)); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const resend = async () => { setBusy(true); setError(''); try { setAttempt(await resendSignInCode(config, attempt)); setCode(''); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  return <main className="auth-layout" id="main">
    <section className="auth-intro"><p className="eyebrow">My requests</p><h1>Check a hardware request.</h1><p>Use the same verified email address or phone number from your submission to view status, approved shipping, tracking, receipts, and reimbursement.</p></section>
    <section className="auth-card" aria-labelledby="signin-title">
      <span className="step-label">{attempt ? 'Step 2 of 2' : 'Step 1 of 2'}</span>
      <h2 id="signin-title">{attempt ? 'Enter your 6-digit code' : 'Contributor sign in'}</h2>
      {message && <Notice type="info">{message}</Notice>}
      {!attempt ? <form onSubmit={send}>
        <p>Use the same verified email or mobile number from your hardware request. It is your login for status, labels, tracking, receipts, and reimbursement.</p>
        <Field label="Email or mobile number" hint="For mobile, include the country code (for example, +1).">
          <input autoFocus autoComplete="username" inputMode="email" value={identifier} onChange={event => setIdentifier(event.target.value)} placeholder="you@example.com or +1 555 123 4567" required />
        </Field>
        {error && <Notice type="error">{error}</Notice>}
        <button className="primary wide" disabled={busy}>{busy ? 'Sending securely…' : 'Send sign-in code'}</button>
        <p className="security-copy">If the address or number can receive a code, it will arrive shortly. For privacy, sign-in messages never confirm whether an account exists.</p>
      </form> : <form onSubmit={verify}>
        <p>We sent a code to the contact method you entered. Codes expire quickly and can be used once.</p>
        <label className="code-label">Security code<input ref={codeRef} className="code-input" aria-describedby="code-hint" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength="6" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} /></label>
        <span id="code-hint" className="field-hint">Enter all 6 digits from the newest message.</span>
        {error && <Notice type="error">{error}</Notice>}
        <button className="primary wide" disabled={busy || code.length !== 6}>{busy ? 'Checking code…' : 'Continue to my requests'}</button>
        <div className="auth-actions"><button type="button" className="text-button" disabled={busy} onClick={resend}>Send a new code</button><button type="button" className="text-button" disabled={busy} onClick={() => { setAttempt(null); setCode(''); setError(''); }}>Use a different contact</button></div>
      </form>}
    </section>
  </main>;
}

function Dashboard({ session, config, onSession, onExpired }) {
  const [requests, setRequests] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const api = async (path, options) => {
    let active = session;
    if (active.expiresAt <= Date.now() + 15000) { active = await refreshSession(config, active); if (!active) return onExpired(); onSession(active); }
    try { return await portalRequest(active, path, options); } catch (err) { if (err.sessionExpired) onExpired(); throw err; }
  };
  const load = async () => { setLoading(true); setError(''); try { setRequests(requestList(await api('/requests'))); } catch (err) { setError(err.message); } finally { setLoading(false); } };
  const open = async request => { setSelected({ ...request, loading: true }); setError(''); try { const data = await api(`/requests/${encodeURIComponent(request.requestId || request.id)}`); setSelected({ ...(data.request || data), timeline: [...(data.history || [])].reverse() }); } catch (err) { setSelected(null); setError(err.message); } };
  useEffect(() => { load(); }, []);
  if (selected) return <main className="workspace" id="main"><button className="back-link" onClick={() => { setSelected(null); load(); }}>← All requests</button>{selected.loading ? <DetailSkeleton /> : <RequestDetail request={selected} api={api} reload={() => open(selected)} />}</main>;
  return <main className="workspace" id="main">
    <div className="dashboard-head"><div><p className="eyebrow">VivintOne</p><h1>My hardware requests</h1><p>Requests submitted with this verified contact appear here.</p></div><div className="session-pill"><span className="session-dot" />Signed in · {sessionMinutes(session)} min</div></div>
    {error && <Notice type="error" action={<button onClick={load}>Try again</button>}>{error}</Notice>}
    {loading ? <RequestSkeleton /> : requests.length ? <div className="request-grid">{requests.map(request => <RequestCard key={request.requestId || request.id} request={request} onClick={() => open(request)} />)}</div> : <EmptyState />}
  </main>;
}

function RequestCard({ request, onClick }) {
  return <article className="request-card"><div className="card-top"><span className={`status status-${request.status}`}>{display(request.status)}</span><time>{date(request.updatedAt || request.createdAt)}</time></div><p className="reference">{request.requestId || request.id}</p><h2>{request.productName || 'Hardware contribution'}</h2><p className="model-number">{request.modelNumber || 'Model details pending'}</p><div className="card-meta"><span><small>Shipping</small>{display(request.carrierStatus || request.shippingMethod)}</span><span><small>Reimbursement</small>{display(request.reimbursementStatus)}</span></div><button className="card-link" onClick={onClick} aria-label={`Open request ${request.requestId || request.id}`}>View request <span>→</span></button></article>;
}

function RequestDetail({ request, api, reload }) {
  const id = request.requestId || request.id;
  const [mode, setMode] = useState('');
  const [notice, setNotice] = useState(null);
  const done = message => { setNotice({ type: 'success', message }); setMode(''); reload(); };
  const fail = error => setNotice({ type: 'error', message: error.message });
  const shippingStarted = request.shippingMethod || request.trackingNumber || request.labelPurchaseState;
  const shippingVisible = ['approved', 'received', 'reimbursed', 'closed'].includes(request.status) && shippingStarted;
  const shippingActionsVisible = request.status === 'approved' && !shippingStarted;
  return <>
    <header className="detail-head"><div><p className="eyebrow">{id}</p><h1>{request.productName || 'Hardware contribution'}</h1><p>{request.modelNumber || 'Model details pending'}{request.quantity > 1 ? ` · Quantity ${request.quantity}` : ''}</p></div><span className={`status status-${request.status}`}>{display(request.status)}</span></header>
    {notice && <Notice type={notice.type}>{notice.message}</Notice>}
    <div className="detail-grid"><Timeline request={request} /><section className="detail-card facts"><h2>Request details</h2><dl><div><dt>Status</dt><dd>{display(request.status)}</dd></div><div><dt>Submitted</dt><dd>{date(request.createdAt) || 'Not available'}</dd></div><div><dt>Offer</dt><dd>{display(request.offerType)}</dd></div><div><dt>Last update</dt><dd>{date(request.updatedAt) || 'Not available'}</dd></div></dl>{request.decisionMessage && <div className="maintainer-note"><strong>Note from Mike</strong><p>{request.decisionMessage}</p></div>}</section></div>
    {(shippingActionsVisible || shippingVisible) && <section className="detail-card shipping"><p className="eyebrow">Approved shipping</p><h2>{shippingStarted ? 'Shipping and reimbursement' : 'Choose how to ship'}</h2>{shippingActionsVisible && <><p>Choose one route. Do not send hardware until this request is approved and these options appear.</p><div className="choice-grid"><button disabled={!request.prepaidLabelAllowed} onClick={() => setMode('prepaid')}><span className="choice-icon">↗</span><strong>Use a prepaid label</strong><span>{request.prepaidLabelAllowed ? 'Enter the packed box and return address. An eligible carrier label is created within the approved safeguards.' : 'A prepaid label is not offered for this request.'}</span></button><button onClick={() => setMode('self')}><span className="choice-icon">⌁</span><strong>Ship it myself</strong><span>Buy your own postage, register tracking, then request reimbursement or waive it.</span></button></div></>}
      {shippingVisible && <ShipmentSummary request={request} api={api} fail={fail} />}
    </section>}
    {mode === 'prepaid' && <PrepaidForm id={id} api={api} done={done} fail={fail} cancel={() => setMode('')} />}
    {mode === 'self' && <TrackingForm id={id} api={api} done={done} fail={fail} cancel={() => setMode('')} />}
    {(request.shippingMethod === 'self_shipped' || request.trackingNumber) && !['submitted','paid','not_requested','waived'].includes(request.reimbursementStatus) && <ReimbursementForm id={id} carrier={request.shippingCarrier || 'Other'} api={api} done={done} fail={fail} />}
  </>;
}

function Timeline({ request }) {
  const received = ['received','reimbursed','closed'].includes(request.status);
  const events = request.timeline || request.events;
  if (events?.length) return <section className="detail-card"><h2>Timeline</h2><ol className="timeline">{events.map((event, index) => <li className="done" key={`${event.createdAt}-${index}`}><span /><div><strong>{event.title || display(event.action || event.status)}</strong>{event.detail && <p>{event.detail}</p>}<time>{date(event.createdAt)}</time></div></li>)}</ol></section>;
  return <section className="detail-card"><h2>Timeline</h2><ol className="timeline">
    <TimelineItem done title="Request received" time={request.createdAt} />
    <TimelineItem done={request.status !== 'submitted'} active={['reviewing','info_requested','on_hold'].includes(request.status)} title={request.status === 'info_requested' ? 'Information requested' : 'Review by Mike'} time={request.reviewedAt} />
    <TimelineItem done={['approved','received','reimbursed','closed'].includes(request.status)} active={request.status === 'approved'} title={request.status === 'declined' ? 'Review complete' : 'Approved to ship'} time={request.decidedAt} />
    <TimelineItem done={request.carrierStatus === 'delivered' || received} active={Boolean(request.trackingNumber) && !received} title={request.trackingNumber ? display(request.carrierStatus) : 'Shipping'} time={request.trackingSubmittedAt || request.labelPurchasedAt} />
    <TimelineItem done={received} active={request.status === 'received'} title="Hardware received" time={request.receivedAt} />
  </ol></section>;
}
function TimelineItem({ done, active, title, time }) { return <li className={done ? 'done' : active ? 'active' : ''}><span /><div><strong>{title}</strong>{time && <time>{date(time)}</time>}</div></li>; }

function ShipmentSummary({ request, api, fail }) {
  const [busy, setBusy] = useState(false);
  const openLabel = async () => { setBusy(true); try { const data = await api(`/requests/${encodeURIComponent(request.requestId || request.id)}/label-url`, { method: 'POST', body: '{}' }); window.location.assign(data.labelUrl); } catch (err) { fail(err); } finally { setBusy(false); } };
  return <div className="shipment-summary"><div><small>Method</small><strong>{request.shippingMethod === 'self_shipped' ? 'Self-paid shipment' : 'Prepaid label'}</strong></div><div><small>Carrier status</small><strong>{display(request.carrierStatus || request.labelPurchaseState)}</strong></div><div><small>Reimbursement</small><strong>{display(request.reimbursementStatus)}</strong></div>{request.trackingNumber && <div><small>Tracking number</small><code>{request.trackingNumber}</code></div>}{request.labelExpiresAt && <div><small>Label available through</small><strong>{date(request.labelExpiresAt)}</strong></div>}<div className="inline-actions">{request.labelAvailable && <button className="primary" onClick={openLabel} disabled={busy}>{busy ? 'Preparing…' : 'Download label'}</button>}{request.trackingUrl && <a className="secondary-button" href={request.trackingUrl} target="_blank" rel="noreferrer">Track package ↗</a>}</div></div>;
}

const initialAddress = { name:'', company:'', street1:'', street2:'', city:'', state:'', zip:'', country:'US', phone:'', length:'', width:'', height:'', weight:'', packageAccurate:false };
function PrepaidForm({ id, api, done, fail, cancel }) {
  const [form, setForm] = useState(initialAddress); const [busy, setBusy] = useState(false); const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const submit = async event => { event.preventDefault(); setBusy(true); try { const data = await api(`/requests/${encodeURIComponent(id)}/rates`, { method:'POST', body:JSON.stringify({ packageAccurate:form.packageAccurate, shippingDetailsAccurate:form.packageAccurate, fromAddress:{ name:form.name,company:form.company,street1:form.street1,street2:form.street2,city:form.city,state:form.state,zip:form.zip,country:form.country,phone:form.phone }, parcel:{ length:form.length,width:form.width,height:form.height,weight:form.weight } }) }); done(data.labelUrl ? 'Your prepaid label is ready to download.' : 'Your label request is under review. No postage was purchased.'); } catch (err) { fail(err); } finally { setBusy(false); } };
  return <form className="detail-card action-form" onSubmit={submit}><FormHeading title="Create a prepaid label" copy="Your return address is shared only with the carrier platform. Measure the packed box before continuing." onClose={cancel} /><div className="form-grid">{['name','company','street1','street2','city','state','zip','phone'].map(key => <Field key={key} className={key.startsWith('street') ? 'full' : ''} label={({name:'Sender name',company:'Company (optional)',street1:'Street address',street2:'Apartment / suite (optional)',city:'City',state:'State / province',zip:'Postal code',phone:'Phone for carrier'})[key]}><input required={!['company','street2'].includes(key)} autoComplete={({name:'name',street1:'address-line1',street2:'address-line2',city:'address-level2',state:'address-level1',zip:'postal-code',phone:'tel'})[key]} value={form[key]} onChange={event => set(key,event.target.value)} /></Field>)}<Field label="Country code"><input maxLength="2" required value={form.country} onChange={event => set('country',event.target.value.toUpperCase())} /></Field>{['length','width','height','weight'].map(key => <Field key={key} label={key === 'weight' ? 'Weight (ounces)' : `${display(key)} (inches)`}><input type="number" min="0.1" step="0.1" required value={form[key]} onChange={event => set(key,event.target.value)} /></Field>)}</div><label className="checkbox"><input type="checkbox" required checked={form.packageAccurate} onChange={event => set('packageAccurate',event.target.checked)} /><span>I confirm the return address and packed dimensions are accurate. Carrier adjustments caused by inaccurate information may not be eligible.</span></label><button className="primary" disabled={busy}>{busy ? 'Validating and creating…' : 'Create prepaid label'}</button></form>;
}

function TrackingForm({ id, api, done, fail, cancel }) {
  const [carrier,setCarrier]=useState('USPS'); const [tracking,setTracking]=useState(''); const [busy,setBusy]=useState(false);
  const submit=async event=>{event.preventDefault();setBusy(true);try{await api(`/requests/${encodeURIComponent(id)}/tracking`,{method:'POST',body:JSON.stringify({carrier,trackingNumber:tracking})});done('Tracking is saved and connected to this request.');}catch(err){fail(err);}finally{setBusy(false);}};
  return <form className="detail-card action-form" onSubmit={submit}><FormHeading title="Register self-paid shipping" copy="Purchase postage directly from your carrier, then enter the tracking details here." onClose={cancel}/><div className="form-grid"><Field label="Carrier"><select value={carrier} onChange={event=>setCarrier(event.target.value)}><option>USPS</option><option>UPS</option><option>FedEx</option><option>Other</option></select></Field><Field label="Tracking number"><input required autoComplete="off" value={tracking} onChange={event=>setTracking(event.target.value)} /></Field></div><button className="primary" disabled={busy}>{busy?'Saving…':'Save tracking'}</button></form>;
}

function ReimbursementForm({ id, carrier, api, done, fail }) {
  const [receipt,setReceipt]=useState(null); const [amount,setAmount]=useState(''); const [method,setMethod]=useState(''); const [destination,setDestination]=useState(''); const [busy,setBusy]=useState(false);
  const waive=async()=>{setBusy(true);try{await api(`/requests/${encodeURIComponent(id)}/reimbursement/waive`,{method:'POST',body:'{}'});done('You chose to cover shipping. No reimbursement will be requested.');}catch(err){fail(err);}finally{setBusy(false);}};
  const submit=async event=>{event.preventDefault();if(!receipt)return;setBusy(true);try{const prepared=await api(`/requests/${encodeURIComponent(id)}/reimbursement/upload`,{method:'POST',body:JSON.stringify({contentType:receipt.type,size:receipt.size})});const form=new FormData();Object.entries(prepared.upload.fields).forEach(([key,value])=>form.append(key,value));form.append('file',receipt);if(!(await fetch(prepared.upload.url,{method:'POST',body:form})).ok)throw new Error('The receipt upload did not finish. Please try again.');await api(`/requests/${encodeURIComponent(id)}/reimbursement`,{method:'POST',body:JSON.stringify({receiptId:prepared.receiptId,amount,paymentMethod:method,paymentDestination:destination,carrier})});done('Your receipt and reimbursement request were received. Payment stays locked until the hardware is physically received.');}catch(err){fail(err);}finally{setBusy(false);}};
  return <form className="detail-card action-form" onSubmit={submit}><FormHeading title="Shipping reimbursement" copy="Reimbursement is optional. Upload the carrier receipt and exact amount paid, or waive reimbursement."/><div className="form-grid"><Field className="full" label="Carrier receipt" hint="JPEG, PNG, or PDF; 10 MB maximum."><input type="file" accept="image/jpeg,image/png,application/pdf" required onChange={event=>setReceipt(event.target.files[0])}/></Field><Field label="Amount paid (USD)"><input type="number" min="0.01" max="500" step="0.01" required value={amount} onChange={event=>setAmount(event.target.value)}/></Field><Field label="Repayment method"><select required value={method} onChange={event=>setMethod(event.target.value)}><option value="">Choose</option><option value="venmo">Venmo</option><option value="zelle">Zelle</option><option value="paypal">PayPal</option></select></Field><Field className="full" label="Payment username, email, or phone" hint="Used only to complete this reimbursement."><input required value={destination} onChange={event=>setDestination(event.target.value)}/></Field></div><div className="split-actions"><button type="button" className="secondary-button" disabled={busy} onClick={waive}>No reimbursement needed</button><button className="primary" disabled={busy}>{busy?'Uploading securely…':'Submit reimbursement'}</button></div></form>;
}

function Shell({ children, session, logout }) { return <><a className="skip" href="#main">Skip to content</a><header className="portal-nav"><a className="brand" href="/">Vivint<span>One</span></a><div className="portal-mark"><span>My requests</span>{session && <button onClick={logout}>Sign out</button>}</div></header>{children}<footer className="portal-footer"><span>VivintOne by Mike Ziolkowski</span><span>Vivint has not created, sponsored, endorsed, or contributed to VivintOne.</span></footer></>; }
function Field({ label,hint,className='',children }) { return <label className={`field ${className}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>; }
function FormHeading({ title,copy,onClose }) { return <div className="form-heading"><div><h2>{title}</h2><p>{copy}</p></div>{onClose&&<button type="button" onClick={onClose} aria-label="Close form">×</button>}</div>; }
function Notice({ type,children,action }) { return <div className={`notice ${type}`} role={type==='error'?'alert':'status'}><span>{children}</span>{action}</div>; }
function Loading({label}) { return <div className="loading" role="status"><span/><p>{label}</p></div>; }
function Centered({children}) { return <main className="centered" id="main">{children}</main>; }
function RequestSkeleton(){return <div className="request-grid" aria-label="Loading requests">{[1,2,3].map(value=><div className="request-card skeleton" key={value}><i/><i/><i/><i/></div>)}</div>;}
function DetailSkeleton(){return <div className="detail-skeleton" aria-label="Loading request"><i/><i/><div><i/><i/></div></div>;}
function EmptyState(){return <section className="empty"><div className="empty-mark">V1</div><h2>No requests are linked yet</h2><p>Hardware requests submitted with this verified contact method will appear here. If you used another email or mobile number, sign out and use that one.</p><a className="primary" href="/#offer">Offer hardware</a></section>;}

createRoot(document.getElementById('root')).render(<App />);
