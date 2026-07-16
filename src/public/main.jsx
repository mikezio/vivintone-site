import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { beginSignIn, clearSession, finishSignIn, getStoredSession, loadAuthConfig, normalizeIdentifier, resendSignInCode, sessionIdentity } from '../portal/auth';
import './styles.css';

const statusLabel = {
  verified_layers: 'Verified layers', partial: 'Partial', wanted: 'Hardware wanted',
  model_verification_needed: 'Model proof needed', research: 'Research', not_supported: 'Not supported',
};

const emptyForm = {
  catalogId: '', productName: '', modelNumber: '', quantity: 1, condition: '',
  factoryReset: '', removedFromAccount: '', offerType: '', testingGoal: '', accessories: '',
  notes: '', photosAvailable: false, name: '', email: '', phone: '', country: 'United States',
  ownsHardware: false, safeToSubmit: false, website: '',
};

function ContactVerification({ authState, verified, onVerified, form, setForm }) {
  const config = authState.config;
  const [mode, setMode] = useState(config?.contributorSmsEnabled ? 'phone' : 'email');
  const [identifier, setIdentifier] = useState('');
  const [attempt, setAttempt] = useState(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const codeRef = useRef(null);
  useEffect(() => { if (config) setMode(config.contributorSmsEnabled ? 'phone' : 'email'); }, [config]);
  const createVerificationIntent = async identity => {
    const response = await fetch('/api/verification-intents', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ identityType: identity.type, identity: identity.username, website: form.website }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.verificationIntentToken) throw new Error('We could not send a verification code. Please try again.');
    return data.verificationIntentToken;
  };
  const send = async () => {
    setBusy(true); setError('');
    try {
      const normalized = normalizeIdentifier(identifier);
      if (normalized.type !== mode) throw new Error(mode === 'phone' ? 'Enter a mobile number with country code.' : 'Enter a valid email address.');
      const value = await beginSignIn(config, identifier, { createVerificationIntent });
      if (value.authenticated) {
        onVerified({ session: value.authenticated, type: value.identity?.type || mode, identity: value.identity?.username || identifier.trim() });
      } else {
        setAttempt(value); setCode(''); setTimeout(() => codeRef.current?.focus(), 0);
      }
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const confirm = async () => {
    setBusy(true); setError('');
    try {
      const session = await finishSignIn(config, attempt, code);
      onVerified({ session, type: attempt.identity.type, identity: attempt.identity.username });
      setAttempt(null);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const resend = async () => { setBusy(true); setError(''); try { setAttempt(await resendSignInCode(config, attempt)); setCode(''); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  const change = () => { clearSession(); onVerified(null); setAttempt(null); setIdentifier(''); setCode(''); setError(''); };

  if (verified) return <div className="verified-contact" role="status"><span className="verified-check" aria-hidden="true">✓</span><div><small>Verified {verified.type === 'phone' ? 'mobile number' : 'email'}</small><strong>{verified.identity}</strong><p>This is your login for request status, labels, tracking, receipts, and reimbursement.</p></div><button type="button" onClick={change}>Change</button></div>;
  if (authState.loading) return <div className="contact-loading" role="status">Preparing secure contact verification…</div>;
  if (!config) return <div className="legacy-contact"><div className="contact-unavailable"><strong>Contact verification is temporarily unavailable.</strong><span>You can still submit by email. This does not create a verified portal login.</span></div><div className="fields"><div className="field"><label htmlFor="email">Email</label><input id="email" type="email" autoComplete="email" required value={form.email} onChange={event => setForm('email', event.target.value)} /></div><div className="field"><label htmlFor="phone">Mobile number (optional)</label><input id="phone" type="tel" autoComplete="tel" pattern="\+[1-9][0-9 ()-]{7,20}" placeholder="+1 555 123 4567" value={form.phone} onChange={event => setForm('phone', event.target.value)} /></div></div></div>;
  return <fieldset className="contact-verification"><legend>Verify your portal login</legend><p>The same verified phone or email is your login for status, labels, tracking, receipts, and reimbursement.</p>
    {!attempt && <><div className={`contact-choices ${config.contributorSmsEnabled ? '' : 'single'}`}>
      {config.contributorSmsEnabled && <label><input type="radio" name="contact-mode" value="phone" checked={mode === 'phone'} onChange={() => { setMode('phone'); setIdentifier(''); }} /><span><strong>Mobile number <em>Recommended</em></strong><small>Receive a text message code.</small></span></label>}
      <label><input type="radio" name="contact-mode" value="email" checked={mode === 'email'} onChange={() => { setMode('email'); setIdentifier(''); }} /><span><strong>Use email instead</strong><small>Receive the code in your inbox.</small></span></label>
    </div><div className="verify-row"><div className="field"><label htmlFor="login-contact">{mode === 'phone' ? 'Mobile number with country code' : 'Email address'}</label><input id="login-contact" type={mode === 'phone' ? 'tel' : 'email'} autoComplete={mode === 'phone' ? 'tel' : 'email'} placeholder={mode === 'phone' ? '+1 555 123 4567' : 'you@example.com'} value={identifier} onChange={event => setIdentifier(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); if (!busy && identifier.trim()) send(); } }} required /></div><button type="button" disabled={busy || !identifier.trim()} onClick={send}>{busy ? 'Sending…' : 'Verify'}</button></div></>}
    {attempt && <div className="contact-code"><div className="code-contact"><span>Code sent to</span><strong>{identifier}</strong><button type="button" disabled={busy} onClick={() => { setAttempt(null); setCode(''); setError(''); }}>Edit</button></div><div className="verify-row"><div className="field"><label htmlFor="contact-code">6-digit code</label><input ref={codeRef} id="contact-code" className="compact-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength="6" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); if (!busy && code.length === 6) confirm(); } }} required /></div><button type="button" disabled={busy || code.length !== 6} onClick={confirm}>{busy ? 'Checking…' : 'Confirm'}</button></div><button type="button" className="resend-code" disabled={busy} onClick={resend}>Send a new code</button></div>}
    {error && <p className="contact-error" role="alert">{error}</p>}
    <p className="enumeration-note">For privacy, verification messages never confirm whether an account already exists.</p>
  </fieldset>;
}

function Catalog({ models, onOffer }) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('All');
  const categories = ['All', ...new Set(models.map(model => model.category))];
  const filtered = useMemo(() => models.filter(model => {
    const haystack = `${model.productName} ${model.modelNumber} ${model.category}`.toLowerCase();
    return (category === 'All' || model.category === category) && haystack.includes(query.toLowerCase());
  }), [models, query, category]);
  return <>
    <div className="catalog-tools">
      <input aria-label="Search device models" placeholder="Search product or model number" value={query} onChange={event => setQuery(event.target.value)} />
      <select aria-label="Filter by category" value={category} onChange={event => setCategory(event.target.value)}>
        {categories.map(value => <option key={value}>{value}</option>)}
      </select>
    </div>
    <div className="catalog" aria-live="polite">
      {filtered.map(model => <article className="model" key={model.id}>
        <div className="model-top"><small>{model.category}</small><span className={`status ${model.status}`}>{statusLabel[model.status] || model.status}</span></div>
        <h3>{model.productName}</h3><code>{model.modelNumber}</code>
        <div className="model-detail"><p><strong>Proven:</strong> {model.tested}</p><p><strong>Next:</strong> {model.needed}</p>
          {model.sourceUrl && <p><a href={model.sourceUrl} target="_blank" rel="noreferrer">Official model evidence ↗</a>{model.evidenceReviewedAt && <small> · reviewed {model.evidenceReviewedAt}</small>}</p>}
          {model.hardwareWanted && <button className="button" onClick={() => onOffer(model)}>Offer this model</button>}
        </div>
      </article>)}
    </div>
  </>;
}

function Intake({ models, settings, preselected, clearPreselected, authState }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [verified, setVerified] = useState(() => {
    const session = getStoredSession();
    const identity = session?.expiresAt > Date.now() ? sessionIdentity(session) : null;
    return identity ? { session, ...identity } : null;
  });
  useEffect(() => {
    if (authState.loading || !verified) return;
    if (!authState.config || (verified.type === 'phone' && !authState.config.contributorSmsEnabled)) setVerified(null);
  }, [authState, verified]);
  const wanted = models.filter(model => model.hardwareWanted);
  useEffect(() => {
    if (!preselected) return;
    setForm(value => ({ ...value, catalogId: preselected.id, productName: preselected.productName, modelNumber: preselected.modelNumber }));
    setStep(1); clearPreselected(); document.querySelector('#offer')?.scrollIntoView();
  }, [preselected, clearPreselected]);
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const chooseModel = id => {
    const model = models.find(item => item.id === id);
    setForm(current => ({ ...current, catalogId: id, productName: model?.productName || '', modelNumber: model?.modelNumber || '' }));
  };
  const next = () => {
    setError('');
    if (step === 1 && (!form.productName.trim() || !form.modelNumber.trim() || !form.condition)) {
      setError('identify the product, model / part number, and condition before continuing');
      return;
    }
    if (step === 2 && (!form.offerType || !form.removedFromAccount || !form.factoryReset)) {
      setError('choose the offer type, account-removal status, and factory-reset status before continuing');
      return;
    }
    setStep(value => Math.min(3, value + 1));
  };
  const back = () => { setError(''); setStep(value => Math.max(1, value - 1)); };
  const submit = async event => {
    event.preventDefault(); setError(''); setSubmitting(true);
    try {
      if (authState.loading && !verified) throw new Error('wait for secure contact verification to finish loading');
      if (authState.config && !verified) throw new Error('verify the phone number or email you will use for the contributor portal');
      if (verified?.session.expiresAt <= Date.now()) { clearSession(); setVerified(null); throw new Error('your verification session expired; verify your contact again'); }
      const verifiedBody = verified ? {
        ...form,
        email: verified.type === 'email' ? verified.identity : '',
        phone: verified.type === 'phone' ? verified.identity : '',
        loginIdentityType: verified.type,
      } : form;
      const response = await fetch(verified ? '/api/portal/requests' : '/api/requests', {
        method: 'POST',
        headers: { 'content-type': 'application/json', ...(verified ? { authorization: `Bearer ${verified.session.idToken}` } : {}) },
        body: JSON.stringify(verifiedBody),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'submission_failed');
      setResult(data);
    } catch (err) { setError(err.message.replaceAll('_', ' ')); }
    finally { setSubmitting(false); }
  };
  if (!settings.acceptingOffers) return <div className="form-panel"><h3>Hardware intake is temporarily paused</h3><p>The compatibility catalog remains available while existing requests are reviewed.</p></div>;
  if (result) return <div className="form-panel success"><span className="eyebrow">Request received</span><h3>Thank you. Nothing should be shipped yet.</h3><strong>{result.reference || result.requestId}</strong><p>I will review the exact model and contact you before any shipping is arranged. Use your verified email or phone number to follow status, approved shipping, tracking, receipts, and reimbursement.</p><a className="button" href="/requests">Open my requests</a></div>;
  return <form className="form-panel" onSubmit={submit}>
    <div className="stepper" aria-label={`Step ${step} of 3`}>{[1,2,3].map(value => <span className={value <= step ? 'active' : ''} key={value} />)}</div>
    {step === 1 && <><h3>1. Identify the hardware</h3><p>Select a known model or enter the printed product number. Never enter a serial number.</p><div className="fields">
      <div className="field full"><label htmlFor="model">Known device</label><select id="model" value={form.catalogId} onChange={event => chooseModel(event.target.value)}><option value="">My model is not listed</option>{wanted.map(model => <option value={model.id} key={model.id}>{model.productName}: {model.modelNumber}</option>)}</select></div>
      <div className="field"><label htmlFor="product">Product name</label><input id="product" required value={form.productName} onChange={event => set('productName', event.target.value)} /></div>
      <div className="field"><label htmlFor="part">Model / part number</label><input id="part" required value={form.modelNumber} onChange={event => set('modelNumber', event.target.value)} /><span className="hint">Non-unique model only. No serial or QR data.</span></div>
      <div className="field"><label htmlFor="quantity">Quantity</label><input id="quantity" type="number" min="1" max="20" value={form.quantity} onChange={event => set('quantity', Number(event.target.value))} /></div>
      <div className="field"><label htmlFor="condition">Condition</label><select id="condition" required value={form.condition} onChange={event => set('condition', event.target.value)}><option value="">Choose</option><option>Working and removed from service</option><option>Working and currently installed</option><option>Unknown / untested</option><option>Not working / for parts</option></select></div>
    </div></>}
    {step === 2 && <><h3>2. Tell us what is available</h3><p>This helps determine whether physical hardware, remote testing, or documentation is most useful.</p><div className="fields">
      <div className="field"><label htmlFor="offer">Offer type</label><select id="offer" required value={form.offerType} onChange={event => set('offerType', event.target.value)}><option value="">Choose</option><option value="donate">Donate permanently</option><option value="loan">Loan for testing</option><option value="remote_test">Keep it and help test remotely</option><option value="unsure">Unsure. Recommend the best option.</option></select></div>
      <div className="field"><label htmlFor="account">Removed from Vivint account?</label><select id="account" required value={form.removedFromAccount} onChange={event => set('removedFromAccount', event.target.value)}><option value="">Choose</option><option>Yes</option><option>No, currently installed</option><option>I can remove it after approval</option><option>Unknown</option></select></div>
      <div className="field"><label htmlFor="reset">Factory-reset status</label><select id="reset" required value={form.factoryReset} onChange={event => set('factoryReset', event.target.value)}><option value="">Choose</option><option>Factory reset</option><option>Not reset</option><option>I can reset it after approval</option><option>Unknown</option></select></div>
      <div className="field"><label htmlFor="photos">Photos</label><select id="photos" value={form.photosAvailable ? 'yes' : 'no'} onChange={event => set('photosAvailable', event.target.value === 'yes')}><option value="no">Not available yet</option><option value="yes">Available if requested privately</option></select></div>
      <div className="field full"><label htmlFor="accessories">Included accessories</label><textarea id="accessories" value={form.accessories} onChange={event => set('accessories', event.target.value)} placeholder="Mount, power supply, bridge, cables…" /></div>
      <div className="field full"><label htmlFor="goal">What should VivintOne test or support?</label><textarea id="goal" value={form.testingGoal} onChange={event => set('testingGoal', event.target.value)} /></div>
    </div></>}
    {step === 3 && <><h3>3. Contact and safety check</h3><p>Your address is not needed. Approved requests receive private shipping instructions later.</p><div className="fields">
      <div className="field full"><label htmlFor="name">Name</label><input id="name" autoComplete="name" required value={form.name} onChange={event => set('name', event.target.value)} /></div>
      <div className="field full"><ContactVerification authState={authState} verified={verified} onVerified={setVerified} form={form} setForm={set} /></div>
      <div className="field full"><label htmlFor="country">Country</label><input id="country" required value={form.country} onChange={event => set('country', event.target.value)} /></div>
      <div className="field full"><label htmlFor="notes">Anything else?</label><textarea id="notes" value={form.notes} onChange={event => set('notes', event.target.value)} /></div>
      <div className="field" style={{position:'absolute',left:'-10000px'}} aria-hidden="true"><label htmlFor="website">Website</label><input id="website" tabIndex="-1" autoComplete="off" value={form.website} onChange={event => set('website', event.target.value)} /></div>
    </div><div className="checks">
      <div className="check"><input id="own" type="checkbox" checked={form.ownsHardware} onChange={event => set('ownsHardware', event.target.checked)} /><label htmlFor="own">I own or am authorized to offer this hardware.</label></div>
      <div className="check"><input id="safe" type="checkbox" checked={form.safeToSubmit} onChange={event => set('safeToSubmit', event.target.checked)} /><label htmlFor="safe">I did not include credentials, PINs, serial numbers, QR codes, keys, MAC addresses, shipping addresses, or account information.</label></div>
    </div></>}
    {error && <p className="error" role="alert">Please check the form: {error}.</p>}
    <div className="form-actions">{step > 1 ? <button type="button" className="back" onClick={back}>Back</button> : <span />}{step < 3 ? <button type="button" onClick={next}>Continue</button> : <button type="submit" disabled={submitting || authState.loading || Boolean(authState.config && !verified)}>{submitting ? 'Sending securely…' : authState.config && !verified ? 'Verify contact to submit' : 'Send for review'}</button>}</div>
  </form>;
}

const GITHUB_URL = 'https://github.com/mikezio/vivintone-home-assistant';

const pageMeta = {
  '/': ['VivintOne', 'A more direct integration path from supported Vivint panels and cameras to Home Assistant, Scrypted, and other tools.'],
  '/compatibility': ['Compatibility · VivintOne', 'Verified VivintOne compatibility by exact device and capability.'],
  '/architecture': ['How it works · VivintOne', 'How VivintOne obtains supported sensor events and camera capabilities for integrations.'],
  '/updates': ['Project updates · VivintOne', 'Current VivintOne development status and project notes.'],
  '/hardware': ['Contribute hardware · VivintOne', 'Offer Vivint hardware for compatibility research and testing.'],
};

const capabilityPaths = {
  sensors: {
    label: 'Sensor events',
    cloud: ['Vivint system', 'Vivint cloud', 'Private API integration', 'Home Assistant'],
    direct: ['Security sensor', 'Vivint panel', 'VivintOne', 'Home Assistant'],
    detail: 'Supported sensor state and events come from the panel that already receives them, then VivintOne normalizes them for Home Assistant.',
    transport: 'Panel service state and pushed event stream',
  },
  media: {
    label: 'Camera media',
    cloud: ['Vivint camera', 'Vivint cloud or panel relay', 'Integration', 'Home Assistant'],
    direct: ['Supported camera', 'VivintOne', 'Home Assistant or Scrypted'],
    detail: 'Supported media capabilities use the camera on the network as the integration source instead of treating a cloud-advertised URL as proof that the whole path is direct.',
    transport: 'Camera-native service on the network',
  },
  analytics: {
    label: 'Camera analytics',
    cloud: ['Vivint camera', 'Hosted event processing', 'Private API integration', 'Home Assistant'],
    direct: ['Supported camera', 'VivintOne', 'Home Assistant or Scrypted'],
    detail: 'Supported on-camera detections are normalized once by VivintOne and exposed to adapters without a separate cloud event subscription becoming the source of truth.',
    transport: 'Camera-native analytics worker',
  },
};

function ArrowIcon() {
  return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h11M11 6l4 4-4 4" /></svg>;
}

function Brand() {
  return <a className="brand" href="/" aria-label="VivintOne home">Vivint<span>One</span><i /></a>;
}

function SiteHeader() {
  return <header className="site-header"><Brand /><nav aria-label="Main navigation">
    <a href="/compatibility">Compatibility</a>
    <a href="/architecture">How it works</a>
    <a href="/updates">Updates</a>
    <a href="/hardware">Contribute</a>
    <a href="/requests">My requests</a>
    <a className="github-link" href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub <span aria-hidden="true">↗</span></a>
  </nav></header>;
}

function SiteFooter() {
  return <footer className="site-footer"><div><Brand /><p>An independent open-source project created and maintained by Mike Ziolkowski.</p><p className="affiliation">Vivint has not created, sponsored, endorsed, or contributed to VivintOne.</p></div><div className="footer-links"><div><strong>Project</strong><a href="/architecture">How it works</a><a href="/compatibility">Compatibility</a><a href="/updates">Updates</a></div><div><strong>Take part</strong><a href="/hardware">Contribute hardware</a><a href="https://github.com/sponsors/mikezio">GitHub Sponsors</a><a href="https://buymeacoffee.com/mzio">Buy Me a Coffee</a></div><div><strong>Account</strong><a href="/requests">My requests</a><a href={GITHUB_URL}>GitHub</a></div></div></footer>;
}

function PageShell({ children }) {
  return <><a className="skip" href="#main">Skip to content</a><SiteHeader /><main id="main">{children}</main><SiteFooter /></>;
}

function PathRow({ name, items, direct }) {
  return <div className={`path-row ${direct ? 'direct' : ''}`}><strong>{name}</strong><div className="path-nodes">{items.map((item, index) => <React.Fragment key={item}><span className={item === 'VivintOne' ? 'core-node' : ''}>{item}</span>{index < items.length - 1 && <i className="path-line" aria-hidden="true"><b /></i>}</React.Fragment>)}</div></div>;
}

function PathComparison({ compact = false }) {
  const [selected, setSelected] = useState('sensors');
  const path = capabilityPaths[selected];
  return <section className={`path-comparison ${compact ? 'compact' : ''}`} aria-labelledby="path-title"><div className="path-heading"><div><p className="section-kicker">Where the integration gets its data</p><h2 id="path-title">The shorter path is the point.</h2></div><p>VivintOne does not replace Vivint services. It gives supported integrations a more direct source.</p></div><div className="capability-tabs" role="tablist" aria-label="Capability path">
    {Object.entries(capabilityPaths).map(([key, value]) => <button key={key} type="button" role="tab" aria-selected={selected === key} onClick={() => setSelected(key)}>{value.label}</button>)}
  </div><div className="path-stage"><PathRow name="Cloud API integration" items={path.cloud} /><PathRow name="VivintOne supported path" items={path.direct} direct /><div className="cloud-continues"><span>Vivint cloud</span><p>Vivint services continue normally. VivintOne does not block or disable them.</p></div></div><div className="path-detail"><p>{path.detail}</p><dl><div><dt>Integration source</dt><dd>{path.transport}</dd></div><div><dt>Project status</dt><dd>Development Preview 6</dd></div></dl></div></section>;
}

function VisitorRoutes() {
  return <section className="visitor-routes" aria-label="Choose where to start"><a href="/architecture"><span>Understand the project</span><strong>See how VivintOne gets supported data and what makes the approach different.</strong><ArrowIcon /></a><a href="/compatibility"><span>Check your hardware</span><strong>Find the exact models and capabilities that have been tested so far.</strong><ArrowIcon /></a><a href="/hardware"><span>Help move it forward</span><strong>Test a device, contribute hardware, or help fund the equipment and hosting.</strong><ArrowIcon /></a></section>;
}

function TestedHardware({ models }) {
  const ids = ['panel-smart-hub-pro-gen2', 'camera-dbc350', 'camera-odc350'];
  const fallback = [
    { id: ids[0], productName: 'Vivint Smart Hub Pro 2', modelNumber: 'VS-SHP200-001 / VS-SHP200-002', status: 'verified_layers' },
    { id: ids[1], productName: 'Vivint Doorbell Camera Pro (Gen 2)', modelNumber: 'VS-DBC350-WHT', status: 'partial' },
    { id: ids[2], productName: 'Vivint Outdoor Camera Pro (Gen 2)', modelNumber: 'VS-ODC350-WHT', status: 'partial' },
  ];
  const selected = ids.map((id, index) => models.find(model => model.id === id) || fallback[index]);
  return <div className="tested-list">{selected.map(model => <a href="/compatibility" key={model.id}><span className="device-glyph" aria-hidden="true" /><span><strong>{model.productName}</strong><small>{model.modelNumber}</small></span><em className={`status ${model.status}`}>{statusLabel[model.status] || model.status}</em><ArrowIcon /></a>)}</div>;
}

function HomePage({ models }) {
  return <PageShell><section className="home-intro"><div className="intro-copy"><div className="preview-label"><span /> Development Preview 6</div><h1>Use Vivint hardware in Home Assistant and Scrypted without taking the long way around.</h1><p>VivintOne gets supported sensor events from the panel and supported camera capabilities from the cameras themselves. Your Vivint system can keep using Vivint's cloud normally.</p><p className="byline">An independent open-source project created and maintained by Mike Ziolkowski.</p><p className="independence-note">Vivint has not created, sponsored, endorsed, or contributed to VivintOne.</p><div className="actions"><a className="button" href="/compatibility">Check compatibility <ArrowIcon /></a><a className="text-link" href="/architecture">How it works <ArrowIcon /></a></div></div><div className="intro-visual" aria-hidden="true"><div className="device panel-device"><span /></div><div className="device camera-device"><span /></div><div className="device sensor-device"><span /></div><svg viewBox="0 0 600 420" preserveAspectRatio="none"><path d="M70 306C190 306 166 112 292 112S410 246 540 246" /><path d="M88 354C238 354 198 224 332 224S426 112 550 112" /><circle cx="292" cy="112" r="5" /><circle cx="332" cy="224" r="5" /></svg><div className="visual-note"><span>Supported integration paths</span><strong>Panel and camera sources</strong></div></div></section><VisitorRoutes /><PathComparison compact /><section className="home-status"><div className="status-copy"><p className="section-kicker">What works today</p><h2>Compatibility is published by capability, not by marketing generation.</h2><p>A connection alone does not make a device fully supported. Each row records what has actually been exercised against physical hardware.</p><a className="text-link" href="/compatibility">View the full compatibility list <ArrowIcon /></a></div><TestedHardware models={models} /><aside className="limit-note"><span>Current limit</span><strong>Controls remain disabled while discovery, state, events, media, and safe rollback are validated.</strong><a href="/updates">Follow development</a></aside></section><section className="project-help"><div><p className="section-kicker">Built in the open, tested on real hardware</p><h2>I need devices, test results, and support to expand what VivintOne can verify.</h2></div><div><p>I test hardware myself and publish compatibility only after the relevant behavior is observed. Contributed devices make it possible to support more models without guessing.</p><div className="help-links"><a href="/hardware">See hardware needed <ArrowIcon /></a><a href="/hardware#offer">Offer a device <ArrowIcon /></a><a href="https://github.com/sponsors/mikezio">Fund hardware and hosting <ArrowIcon /></a></div></div></section></PageShell>;
}

function CompatibilityPage({ models, loadError, onOffer }) {
  return <PageShell><section className="page-intro"><p className="section-kicker">Compatibility</p><h1>What has actually been tested.</h1><p>Support is recorded by exact product family and capability. A verified event path does not automatically mean media, settings, or controls are also ready.</p></section><section className="catalog-section">{loadError ? <p className="error" role="alert">{loadError}</p> : <Catalog models={models} onOffer={model => { onOffer(model); window.location.href = `/hardware?model=${encodeURIComponent(model.id)}#offer`; }} />}</section></PageShell>;
}

function ArchitecturePage() {
  return <PageShell><section className="page-intro"><p className="section-kicker">How it works</p><h1>VivintOne changes where the integration gets supported data.</h1><p>It does not claim that a Vivint system stops using Vivint's cloud. It connects integrations to the panel and supported cameras on the same network instead of making hosted private APIs the normal source for every feature.</p></section><PathComparison /><section className="architecture-copy"><article><span>01</span><h2>Sensors report to the panel.</h2><p>Door contacts, motion sensors, glass-break sensors, and similar devices normally use the Vivint panel as their gateway. VivintOne reads supported service state from that panel and receives its pushed event stream.</p></article><article><span>02</span><h2>Cameras have their own path.</h2><p>Supported camera media and onboard analytics use camera-native services. Camera capabilities are evaluated separately from panel sensor events and separately from any hosted fallback.</p></article><article><span>03</span><h2>One core normalizes the result.</h2><p>Home Assistant, Scrypted, and other adapters consume the same capability model. Each capability reports its transport and verification status instead of labeling a whole device “local” after one successful feature.</p></article><article><span>04</span><h2>Vivint services remain intact.</h2><p>Account authentication, enrollment, and normal Vivint operation can still involve Vivint's cloud. VivintOne does not block that traffic or promise that all system data remains inside the home.</p></article></section></PageShell>;
}

function UpdatesPage() {
  return <PageShell><section className="page-intro"><p className="section-kicker">Project updates</p><h1>What is being built and what still needs proof.</h1><p>No launch theater and no inflated roadmap. Updates will document verified progress, remaining limits, and changes that affect people testing the development preview.</p></section><section className="updates-layout"><article className="current-update"><span>Current development line</span><h2>Development Preview 6</h2><p>The current work is validating authorized onboarding, panel-local inventory and events, camera-native analytics workers, recovery, and safe removal of only the access created by a development installation.</p><div className="limit-note"><span>Still pending</span><strong>Fresh physical camera analytics events must be observed end to end before that path is considered release-ready. Device controls remain disabled.</strong></div><a className="button" href={GITHUB_URL}>Read the repository notes <ArrowIcon /></a></article><aside><h2>What future updates will include</h2><ul><li>New hardware and exact model verification</li><li>Capability-specific test results</li><li>Setup, repair, and rollback changes</li><li>Release notes and known limitations</li></ul><p>Email subscriptions will be added here after the update publishing flow is ready.</p></aside></section></PageShell>;
}

function HardwarePage({ models, settings, selected, clearSelected, loadError, authState }) {
  return <PageShell><section className="page-intro hardware-heading"><p className="section-kicker">Contribute hardware</p><h1>Help me test the models I cannot verify yet.</h1><p>I use contributed and loaned hardware to identify exact product revisions, implement capabilities, and repeat tests before publishing support.</p><div className="hardware-rules"><strong>Do not ship anything until I accept the request.</strong><span>Approved requests receive private shipping instructions and can use a prepaid label or request eligible reimbursement after the device is received.</span></div></section><section className="wanted-hardware"><div><h2>Hardware currently needed</h2><p>Search the compatibility list for models marked “Hardware wanted,” or submit an exact model that is not listed yet.</p><a className="text-link" href="/compatibility">Browse compatibility <ArrowIcon /></a></div><div className="wanted-preview">{models.filter(model => model.hardwareWanted).slice(0, 6).map(model => <button key={model.id} type="button" onClick={() => { clearSelected(model); document.querySelector('#offer')?.scrollIntoView(); }}><span>{model.category}</span><strong>{model.productName}</strong><small>{model.modelNumber}</small></button>)}</div></section><section className="intake" id="offer"><div className="form-intro"><p className="section-kicker">Hardware offer</p><h2>{settings.title}</h2><p>{settings.intro}</p><div className="privacy"><strong>Keep private information out of the form.</strong><br />{settings.privacy}</div></div>{loadError ? <p className="error" role="alert">{loadError}</p> : <Intake models={models} settings={settings} preselected={selected} clearPreselected={() => clearSelected(null)} authState={authState} />}</section><section className="support"><div><p className="section-kicker">Prefer to support the work another way?</p><h2>Hardware, hosting, and testing are paid for personally.</h2></div><div className="support-copy"><p>Financial support helps cover devices, accessories, shipping, and the infrastructure used to build and test VivintOne.</p><div className="actions"><a className="button" href="https://github.com/sponsors/mikezio">GitHub Sponsors <ArrowIcon /></a><a className="text-link" href="https://buymeacoffee.com/mzio">Buy Me a Coffee <ArrowIcon /></a></div><p className="support-note">Support is voluntary and does not purchase compatibility, priority, or access to private research.</p></div></section></PageShell>;
}

function NotFoundPage() {
  return <PageShell><section className="page-intro"><p className="section-kicker">Page not found</p><h1>That page is not part of VivintOne.</h1><a className="button" href="/">Return to the project <ArrowIcon /></a></section></PageShell>;
}

function App() {
  const [models, setModels] = useState([]);
  const [settings, setSettings] = useState({ title: 'Help expand VivintOne hardware support', intro: '', privacy: '', acceptingOffers: true });
  const [selected, setSelected] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [authState, setAuthState] = useState({ loading: true, config: null });
  const pathname = window.location.pathname.replace(/\/$/, '') || '/';

  useEffect(() => {
    const [title, description] = pageMeta[pathname] || ['Page not found · VivintOne', 'VivintOne project site.'];
    document.title = title;
    document.querySelector('meta[name="description"]')?.setAttribute('content', description);
    if (pathname === '/' && window.location.hash === '#offer') window.location.replace('/hardware#offer');
  }, [pathname]);
  useEffect(() => { loadAuthConfig().then(config => setAuthState({ loading: false, config })).catch(() => setAuthState({ loading: false, config: null })); }, []);
  useEffect(() => {
    Promise.all([
      fetch('/api/catalog').then(response => response.ok ? response.json() : Promise.reject(new Error('catalog'))),
      fetch('/api/public-settings').then(response => response.ok ? response.json() : Promise.reject(new Error('settings'))),
    ]).then(([catalog, publicSettings]) => {
      setModels(catalog.models || []);
      setSettings(current => ({...current, ...publicSettings}));
    }).catch(() => setLoadError('The live compatibility catalog is temporarily unavailable. Please try again shortly.'));
  }, []);

  if (pathname === '/requests') { window.location.replace('/portal.html'); return null; }
  if (pathname === '/') return <HomePage models={models} />;
  if (pathname === '/compatibility') return <CompatibilityPage models={models} loadError={loadError} onOffer={setSelected} />;
  if (pathname === '/architecture') return <ArchitecturePage />;
  if (pathname === '/updates') return <UpdatesPage />;
  if (pathname === '/hardware') {
    const requestedModel = new URLSearchParams(window.location.search).get('model');
    const selectedModel = selected || models.find(model => model.id === requestedModel) || null;
    return <HardwarePage models={models} settings={settings} selected={selectedModel} clearSelected={setSelected} loadError={loadError} authState={authState} />;
  }
  return <NotFoundPage />;
}

createRoot(document.getElementById('root')).render(<App />);
