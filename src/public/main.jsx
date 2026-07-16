import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const statusLabel = {
  verified_layers: 'Verified layers', partial: 'Partial', wanted: 'Hardware wanted',
  model_verification_needed: 'Model proof needed', research: 'Research', not_supported: 'Not supported',
};

const emptyForm = {
  catalogId: '', productName: '', modelNumber: '', quantity: 1, condition: '',
  factoryReset: '', removedFromAccount: '', offerType: '', testingGoal: '', accessories: '',
  notes: '', photosAvailable: false, name: '', email: '', country: 'United States',
  ownsHardware: false, safeToSubmit: false, website: '',
};

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

function Intake({ models, settings, preselected, clearPreselected }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false);
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
      const response = await fetch('/api/requests', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(form) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'submission_failed');
      setResult(data);
    } catch (err) { setError(err.message.replaceAll('_', ' ')); }
    finally { setSubmitting(false); }
  };
  if (!settings.acceptingOffers) return <div className="form-panel"><h3>Hardware intake is temporarily paused</h3><p>The compatibility catalog remains available while existing requests are reviewed.</p></div>;
  if (result) return <div className="form-panel success"><span className="eyebrow">Request received</span><h3>Thank you—nothing should be shipped yet.</h3><strong>{result.reference}</strong><p>A confirmation was sent to your email. The VivintOne Hardware Lab will review the exact model and reply with a decision. Shipping information is provided only after approval.</p></div>;
  return <form className="form-panel" onSubmit={submit}>
    <div className="stepper" aria-label={`Step ${step} of 3`}>{[1,2,3].map(value => <span className={value <= step ? 'active' : ''} key={value} />)}</div>
    {step === 1 && <><h3>1. Identify the hardware</h3><p>Select a known model or enter the printed product number. Never enter a serial number.</p><div className="fields">
      <div className="field full"><label htmlFor="model">Known device</label><select id="model" value={form.catalogId} onChange={event => chooseModel(event.target.value)}><option value="">My model is not listed</option>{wanted.map(model => <option value={model.id} key={model.id}>{model.productName} — {model.modelNumber}</option>)}</select></div>
      <div className="field"><label htmlFor="product">Product name</label><input id="product" required value={form.productName} onChange={event => set('productName', event.target.value)} /></div>
      <div className="field"><label htmlFor="part">Model / part number</label><input id="part" required value={form.modelNumber} onChange={event => set('modelNumber', event.target.value)} /><span className="hint">Non-unique model only. No serial or QR data.</span></div>
      <div className="field"><label htmlFor="quantity">Quantity</label><input id="quantity" type="number" min="1" max="20" value={form.quantity} onChange={event => set('quantity', Number(event.target.value))} /></div>
      <div className="field"><label htmlFor="condition">Condition</label><select id="condition" required value={form.condition} onChange={event => set('condition', event.target.value)}><option value="">Choose</option><option>Working and removed from service</option><option>Working and currently installed</option><option>Unknown / untested</option><option>Not working / for parts</option></select></div>
    </div></>}
    {step === 2 && <><h3>2. Tell us what is available</h3><p>This helps determine whether physical hardware, remote testing, or documentation is most useful.</p><div className="fields">
      <div className="field"><label htmlFor="offer">Offer type</label><select id="offer" required value={form.offerType} onChange={event => set('offerType', event.target.value)}><option value="">Choose</option><option value="donate">Donate permanently</option><option value="loan">Loan for testing</option><option value="remote_test">Keep it and help test remotely</option><option value="unsure">Unsure—recommend the best option</option></select></div>
      <div className="field"><label htmlFor="account">Removed from Vivint account?</label><select id="account" required value={form.removedFromAccount} onChange={event => set('removedFromAccount', event.target.value)}><option value="">Choose</option><option>Yes</option><option>No, currently installed</option><option>I can remove it after approval</option><option>Unknown</option></select></div>
      <div className="field"><label htmlFor="reset">Factory-reset status</label><select id="reset" required value={form.factoryReset} onChange={event => set('factoryReset', event.target.value)}><option value="">Choose</option><option>Factory reset</option><option>Not reset</option><option>I can reset it after approval</option><option>Unknown</option></select></div>
      <div className="field"><label htmlFor="photos">Photos</label><select id="photos" value={form.photosAvailable ? 'yes' : 'no'} onChange={event => set('photosAvailable', event.target.value === 'yes')}><option value="no">Not available yet</option><option value="yes">Available if requested privately</option></select></div>
      <div className="field full"><label htmlFor="accessories">Included accessories</label><textarea id="accessories" value={form.accessories} onChange={event => set('accessories', event.target.value)} placeholder="Mount, power supply, bridge, cables…" /></div>
      <div className="field full"><label htmlFor="goal">What should VivintOne test or support?</label><textarea id="goal" value={form.testingGoal} onChange={event => set('testingGoal', event.target.value)} /></div>
    </div></>}
    {step === 3 && <><h3>3. Contact and safety check</h3><p>Your address is not needed. Approved requests receive private shipping instructions later.</p><div className="fields">
      <div className="field"><label htmlFor="name">Name</label><input id="name" required value={form.name} onChange={event => set('name', event.target.value)} /></div>
      <div className="field"><label htmlFor="email">Email</label><input id="email" type="email" required value={form.email} onChange={event => set('email', event.target.value)} /></div>
      <div className="field full"><label htmlFor="country">Country</label><input id="country" required value={form.country} onChange={event => set('country', event.target.value)} /></div>
      <div className="field full"><label htmlFor="notes">Anything else?</label><textarea id="notes" value={form.notes} onChange={event => set('notes', event.target.value)} /></div>
      <div className="field" style={{position:'absolute',left:'-10000px'}} aria-hidden="true"><label htmlFor="website">Website</label><input id="website" tabIndex="-1" autoComplete="off" value={form.website} onChange={event => set('website', event.target.value)} /></div>
    </div><div className="checks">
      <div className="check"><input id="own" type="checkbox" checked={form.ownsHardware} onChange={event => set('ownsHardware', event.target.checked)} /><label htmlFor="own">I own or am authorized to offer this hardware.</label></div>
      <div className="check"><input id="safe" type="checkbox" checked={form.safeToSubmit} onChange={event => set('safeToSubmit', event.target.checked)} /><label htmlFor="safe">I did not include credentials, PINs, serial numbers, QR codes, keys, MAC addresses, shipping addresses, or account information.</label></div>
    </div></>}
    {error && <p className="error" role="alert">Please check the form: {error}.</p>}
    <div className="form-actions">{step > 1 ? <button type="button" className="back" onClick={back}>Back</button> : <span />}{step < 3 ? <button type="button" onClick={next}>Continue</button> : <button type="submit" disabled={submitting}>{submitting ? 'Sending securely…' : 'Send for review'}</button>}</div>
  </form>;
}

function App() {
  const [models, setModels] = useState([]);
  const [settings, setSettings] = useState({ title: 'Help expand VivintOne hardware support', intro: '', privacy: '', acceptingOffers: true });
  const [selected, setSelected] = useState(null);
  const [loadError, setLoadError] = useState('');
  useEffect(() => {
    Promise.all([
      fetch('/api/catalog').then(response => response.ok ? response.json() : Promise.reject(new Error('catalog'))),
      fetch('/api/public-settings').then(response => response.ok ? response.json() : Promise.reject(new Error('settings'))),
    ]).then(([catalog, publicSettings]) => {
      setModels(catalog.models || []);
      setSettings(current => ({...current, ...publicSettings}));
    }).catch(() => setLoadError('The live compatibility catalog is temporarily unavailable. Please try again shortly.'));
  }, []);
  return <><a className="skip" href="#main">Skip to content</a><header className="nav"><a className="brand" href="#top">Vivint<span>One</span> / Hardware Lab</a><div><a href="#compatibility">Compatibility</a><a href="#offer">Offer hardware</a><a href="#support">Support</a><a href="https://github.com/mikezio/vivintone-home-assistant">GitHub</a></div></header><main id="main">
    <section className="hero" id="top"><div><p className="eyebrow">Evidence-driven compatibility</p><h1>Real hardware. Real proof.</h1><p className="hero-copy">VivintOne tests each product family and each native capability instead of calling an entire generation supported after one successful connection.</p><div className="actions"><a className="button" href="#offer">Offer a device</a><a className="button secondary" href="#compatibility">View tested models</a></div></div><aside className="hero-card"><strong>What happens to contributed hardware?</strong><ul><li>Exact model and generation identification</li><li>Local discovery and event validation</li><li>Media, audio, analytics, settings, and control testing</li><li>Repeatable regression coverage before compatibility is published</li></ul></aside></section>
    <section className="catalog-section" id="compatibility"><div className="section-head"><div><p className="eyebrow">Living compatibility catalog</p><h2>What works—and what still needs proof.</h2></div><p>“Verified” applies only to the layers named on each card. Hardware-wanted models are priorities for physical integration and repeatable testing.</p></div>{loadError ? <p className="error" role="alert">{loadError}</p> : <Catalog models={models} onOffer={setSelected} />}</section>
    <section className="intake" id="offer"><div className="form-intro"><p className="eyebrow">Hardware contribution intake</p><h2>{settings.title}</h2><p>{settings.intro}</p><div className="privacy"><strong>Privacy boundary</strong><br />{settings.privacy}</div></div><Intake models={models} settings={settings} preselected={selected} clearPreselected={() => setSelected(null)} /></section>
    <section className="support" id="support"><div><p className="eyebrow">Support independent development</p><h2>Help VivintOne keep moving.</h2></div><div className="support-copy"><p>Voluntary support helps fund research, test equipment, infrastructure, and the time required to build and maintain VivintOne.</p><div className="actions"><a className="button" href="https://buymeacoffee.com/mzio" target="_blank" rel="noreferrer">Buy Me a Coffee ↗</a><a className="button secondary" href="https://github.com/sponsors/mikezio" target="_blank" rel="noreferrer">GitHub Sponsors ↗</a></div><p className="support-note"><strong>Separate from hardware shipping:</strong> project support is never a shipping payment or reimbursement, and it does not purchase compatibility, priority, or access to private research.</p></div></section>
  </main><footer className="footer"><span>VivintOne by Mike Ziolkowski</span><span>Independent community project; not affiliated with Vivint.</span></footer></>;
}

createRoot(document.getElementById('root')).render(<App />);
