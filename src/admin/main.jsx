import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { UserManager, WebStorageStateStore } from 'oidc-client-ts';
import { useCollection } from '@cloudscape-design/collection-hooks';
import '@cloudscape-design/global-styles/index.css';
import {
  AppLayout, Badge, Box, Button, Checkbox, ColumnLayout, Container, ContentLayout,
  Flashbar, FormField, Header, Input, Link, Modal, Pagination, Select, SideNavigation,
  SpaceBetween, StatusIndicator, Table, Textarea, TextFilter, Toggle,
} from '@cloudscape-design/components';
import './styles.css';

const blankModel = {
  id: '', category: '', productName: '', modelNumber: '', generation: '', status: 'wanted',
  hardwareWanted: true, tested: '', needed: '', sourceUrl: '', evidenceReviewedAt: '', position: 999,
};

const statusOptions = [
  ['submitted', 'Submitted'], ['reviewing', 'Reviewing'], ['info_requested', 'Info requested'],
  ['on_hold', 'On hold'], ['approved', 'Approved'], ['declined', 'Declined'],
  ['received', 'Physically received'], ['reimbursed', 'Reimbursed'], ['closed', 'Closed'],
].map(([value, label]) => ({ value, label }));

const catalogStatuses = [
  ['verified_layers', 'Verified layers'], ['partial', 'Partial'], ['wanted', 'Hardware wanted'],
  ['model_verification_needed', 'Model proof needed'], ['research', 'Research'], ['not_supported', 'Not supported'],
].map(([value, label]) => ({ value, label }));

const decisionLabels = { approve: 'Approve and send shipping details', decline: 'Decline', hold: 'Place on hold', request_info: 'Request information' };
const statusType = status => ({ approved: 'success', received: 'success', declined: 'error', info_requested: 'info', on_hold: 'stopped', reviewing: 'in-progress' }[status] || 'pending');

function displayError(error) {
  const message = error?.message || String(error);
  return message.replaceAll('_', ' ');
}

function App() {
  const [config, setConfig] = useState(null);
  const [manager, setManager] = useState(null);
  const [user, setUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [section, setSection] = useState(location.hash.replace('#', '') || 'queue');
  const [flashes, setFlashes] = useState([]);

  useEffect(() => {
    fetch('/config.json', { cache: 'no-store' }).then(response => response.json()).then(value => {
      if (String(value.clientId).startsWith('__')) throw new Error('The admin application is not deployed yet.');
      const next = new UserManager({
        authority: value.authority,
        client_id: value.clientId,
        redirect_uri: value.redirectUri,
        post_logout_redirect_uri: value.postLogoutRedirectUri,
        response_type: 'code',
        scope: 'openid email intake/admin',
        userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      });
      setConfig(value); setManager(next);
      return location.search.includes('code=') ? next.signinRedirectCallback().then(() => history.replaceState({}, '', '/admin.html')) : next.getUser();
    }).then(current => { setUser(current && !current.expired ? current : null); setAuthReady(true); })
      .catch(error => { setFlashes([{ type: 'error', content: displayError(error), dismissible: true }]); setAuthReady(true); });
  }, []);

  const notify = (type, content) => setFlashes([{ type, content, dismissible: true, onDismiss: () => setFlashes([]) }]);
  const api = async (path, options = {}) => {
    let current = user;
    if (!current || current.expired) current = await manager?.signinSilent();
    const response = await fetch(path, {
      ...options,
      headers: { 'content-type': 'application/json', authorization: `Bearer ${current?.access_token}`, ...(options.headers || {}) },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `request_failed_${response.status}`);
    return data;
  };

  if (!authReady) return <div className="auth-screen"><StatusIndicator type="loading">Loading the private workspace</StatusIndicator></div>;
  if (!user) return <div className="auth-screen"><Container header={<Header variant="h1">VivintOne Hardware Lab</Header>}><SpaceBetween size="l"><p>This private workspace is only for the project maintainer. Sign-in uses a short-lived authorization-code flow with PKCE.</p><Flashbar items={flashes} /><Button variant="primary" disabled={!manager || !config} onClick={() => manager.signinRedirect()}>Sign in</Button><Link href="/">Return to the public catalog</Link></SpaceBetween></Container></div>;

  const content = section === 'catalog' ? <Catalog api={api} notify={notify} /> : section === 'settings' ? <Settings api={api} notify={notify} /> : <Queue api={api} notify={notify} />;
  return <AppLayout
    navigation={<SideNavigation header={{ href: '/', text: 'VivintOne Hardware Lab' }} activeHref={`#${section}`} onFollow={event => { event.preventDefault(); const next = event.detail.href.slice(1); location.hash = next; setSection(next); }} items={[
      { type: 'link', text: 'Request queue', href: '#queue' },
      { type: 'link', text: 'Compatibility catalog', href: '#catalog' },
      { type: 'link', text: 'Site and email settings', href: '#settings' },
      { type: 'divider' },
      { type: 'link', text: 'Public site', href: '/' },
    ]} />}
    toolsHide content={<ContentLayout header={<Header variant="h1" actions={<Button onClick={() => manager.signoutRedirect()}>Sign out</Button>}>Maintainer workspace</Header>}><Flashbar items={flashes} />{content}</ContentLayout>}
  />;
}

function Queue({ api, notify }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState([]);
  const [detail, setDetail] = useState(null);
  const [decision, setDecision] = useState(null);
  const [message, setMessage] = useState('');
  const [prepaidLabelAllowed, setPrepaidLabelAllowed] = useState(true);
  const [labelMaxAmount, setLabelMaxAmount] = useState('40.00');
  const load = () => { setLoading(true); api('/api/admin/requests').then(data => setItems(data.requests || [])).catch(error => notify('error', displayError(error))).finally(() => setLoading(false)); };
  useEffect(load, []);
  useEffect(() => {
    const requested = new URLSearchParams(location.search).get('request');
    if (requested) api(`/api/admin/requests/${encodeURIComponent(requested)}`).then(setDetail).catch(error => notify('error', displayError(error)));
  }, []);
  const { items: shown, collectionProps, filterProps, paginationProps } = useCollection(items, { filtering: { empty: 'No hardware requests yet.', noMatch: 'No requests match this search.' }, pagination: { pageSize: 20 }, sorting: { defaultState: { sortingColumn: { sortingField: 'createdAt' }, isDescending: true } } });
  const open = item => api(`/api/admin/requests/${encodeURIComponent(item.requestId)}`).then(setDetail).catch(error => notify('error', displayError(error)));
  const exportCsv = () => {
    const headers = ['reference', 'created', 'status', 'name', 'email', 'product', 'model', 'offer'];
    const rows = items.map(item => [item.requestId, item.createdAt, item.status, item.name, item.email, item.productName, item.modelNumber, item.offerType]);
    const csv = [headers, ...rows].map(row => row.map(value => `"${String(value || '').replaceAll('"', '""')}"`).join(',')).join('\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); link.download = 'vivintone-hardware-requests.csv'; link.click(); URL.revokeObjectURL(link.href);
  };
  const decide = async () => {
    try {
      const data = await api(`/api/admin/requests/${encodeURIComponent(detail.request.requestId)}/decision`, { method: 'POST', body: JSON.stringify({ decision, message, prepaidLabelAllowed: decision === 'approve' && prepaidLabelAllowed, labelMaxAmount }) });
      setDetail(data); setDecision(null); setMessage(''); notify('success', `Request ${data.request.requestId} updated and any applicable email sent.`); load();
    } catch (error) { notify('error', displayError(error)); }
  };
  const saveNotes = async request => {
    try { const data = await api(`/api/admin/requests/${encodeURIComponent(request.requestId)}`, { method: 'PATCH', body: JSON.stringify({ status: request.status, adminNotes: request.adminNotes }) }); setDetail(data); notify('success', 'Private notes and status saved.'); load(); }
    catch (error) { notify('error', displayError(error)); }
  };
  return <SpaceBetween size="l"><Table {...collectionProps} loading={loading} loadingText="Loading requests" items={shown} selectionType="single" selectedItems={selected} onSelectionChange={event => setSelected(event.detail.selectedItems)}
    header={<Header counter={`(${items.length})`} actions={<SpaceBetween direction="horizontal" size="xs"><Button onClick={exportCsv}>Export CSV</Button><Button onClick={load}>Refresh</Button><Button variant="primary" disabled={!selected.length} onClick={() => open(selected[0])}>Review</Button></SpaceBetween>}>Hardware requests</Header>}
    filter={<TextFilter {...filterProps} filteringPlaceholder="Find a reference, person, product, or model" />}
    pagination={<Pagination {...paginationProps} />}
    columnDefinitions={[
      { id: 'reference', header: 'Reference', cell: item => <Link onFollow={() => open(item)}>{item.requestId}</Link>, sortingField: 'requestId' },
      { id: 'status', header: 'Status', cell: item => <StatusIndicator type={statusType(item.status)}>{item.status.replaceAll('_', ' ')}</StatusIndicator>, sortingField: 'status' },
      { id: 'product', header: 'Hardware', cell: item => <><strong>{item.productName}</strong><br /><small>{item.modelNumber}</small></>, sortingField: 'productName' },
      { id: 'person', header: 'Contributor', cell: item => <>{item.name}<br /><small>{item.country}</small></>, sortingField: 'name' },
      { id: 'created', header: 'Received', cell: item => new Date(item.createdAt).toLocaleString(), sortingField: 'createdAt' },
    ]} />
    {detail && <RequestReview data={detail} setData={setDetail} close={() => setDetail(null)} save={saveNotes} openDecision={value => { setDecision(value); setMessage(''); setPrepaidLabelAllowed(true); setLabelMaxAmount('40.00'); }} api={api} notify={notify} reload={load} />}
    <Modal visible={Boolean(decision)} onDismiss={() => setDecision(null)} header={decisionLabels[decision] || 'Update request'} footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs"><Button onClick={() => setDecision(null)}>Cancel</Button><Button variant="primary" onClick={decide}>Confirm</Button></SpaceBetween></Box>}>
      <SpaceBetween size="m"><p>{decision === 'approve' ? 'The contributor will receive the private shipping instructions saved in Settings. You can authorize one self-service prepaid label with a hard spending cap.' : 'This message is included in the contributor email when applicable.'}</p>{decision === 'approve' && <ColumnLayout columns={2}><FormField label="Self-service prepaid label"><Toggle checked={prepaidLabelAllowed} onChange={event => setPrepaidLabelAllowed(event.detail.checked)}>Authorize one label for this request</Toggle></FormField><FormField label="Maximum label cost (USD)" description="Anything above this amount stops for review."><Input type="number" value={labelMaxAmount} disabled={!prepaidLabelAllowed} onChange={event => setLabelMaxAmount(event.detail.value)} /></FormField></ColumnLayout>}<FormField label="Personal note"><Textarea value={message} onChange={event => setMessage(event.detail.value)} rows={7} /></FormField></SpaceBetween>
    </Modal>
  </SpaceBetween>;
}

function RequestReview({ data, setData, close, save, openDecision, api, notify, reload }) {
  const item = data.request;
  const [rateId, setRateId] = useState(item.prepaidRates?.[0]?.id || '');
  const chosenRate = item.prepaidRates?.find(rate => rate.id === rateId);
  const update = (key, value) => setData(current => ({ ...current, request: { ...current.request, [key]: value } }));
  const refresh = next => { setData(next); reload(); };
  const receipt = async () => { try { const result = await api(`/api/admin/requests/${encodeURIComponent(item.requestId)}/receipt-url`, { method: 'POST' }); window.open(result.url, '_blank', 'noopener,noreferrer'); } catch (error) { notify('error', displayError(error)); } };
  const buyLabel = async () => { if (!chosenRate) return; const billing = chosenRate.carrier === 'USPS' ? 'USPS is generally charged when the label is created.' : 'UPS and FedEx are generally billed when the carrier scans the package.'; if (!confirm(`Override the request cap and authorize ${chosenRate.amount} ${chosenRate.currency} for ${chosenRate.carrier} ${chosenRate.service}? ${billing} Only one active label is allowed for this request.`)) return; try { const result = await api(`/api/admin/requests/${encodeURIComponent(item.requestId)}/label`, { method: 'POST', body: JSON.stringify({ rateId, expectedAmount: chosenRate.amount, expectedCarrier: chosenRate.carrier, confirmPurchase: true, confirmOverCap: true }) }); refresh(result); notify('success', 'Prepaid label purchased and emailed to the contributor.'); } catch (error) { notify('error', displayError(error)); } };
  const reimbursed = async () => { if (!confirm(`Confirm the hardware is physically present and ${item.reimbursementAmount} USD has been sent through ${item.paymentMethod}?`)) return; try { const result = await api(`/api/admin/requests/${encodeURIComponent(item.requestId)}/reimbursed`, { method: 'POST', body: JSON.stringify({ note: 'Payment sent after physical receipt confirmation.' }) }); refresh(result); notify('success', 'Reimbursement marked paid and the contributor was emailed.'); } catch (error) { notify('error', displayError(error)); } };
  return <Container header={<Header variant="h2" actions={<Button onClick={close}>Close</Button>}>{item.requestId}</Header>}><SpaceBetween size="l">
    <ColumnLayout columns={3} variant="text-grid"><div><Box variant="awsui-key-label">Status</Box><StatusIndicator type={statusType(item.status)}>{item.status.replaceAll('_', ' ')}</StatusIndicator></div><div><Box variant="awsui-key-label">Contributor</Box>{item.name}<br /><Link href={`mailto:${item.email}`}>{item.email}</Link><br />{item.country}</div><div><Box variant="awsui-key-label">Offer</Box>{item.quantity} × {item.productName}<br /><code>{item.modelNumber}</code><br />{item.offerType.replaceAll('_', ' ')}</div></ColumnLayout>
    <ColumnLayout columns={2}><div><Box variant="awsui-key-label">Condition</Box>{item.condition}<br /><Box variant="awsui-key-label">Account removal</Box>{item.removedFromAccount}<br /><Box variant="awsui-key-label">Factory reset</Box>{item.factoryReset}</div><div><Box variant="awsui-key-label">Testing goal</Box><p className="preserve">{item.testingGoal || 'Not provided'}</p><Box variant="awsui-key-label">Accessories / notes</Box><p className="preserve">{[item.accessories, item.notes].filter(Boolean).join('\n') || 'Not provided'}</p></div></ColumnLayout>
    {(item.shippingMethod || item.reimbursementStatus) && <Container header={<Header variant="h3">Shipping and reimbursement</Header>}><SpaceBetween size="m"><ColumnLayout columns={3}><div><Box variant="awsui-key-label">Method</Box>{item.shippingMethod?.replaceAll('_', ' ') || 'Not selected'}<br /><Box variant="awsui-key-label">Carrier status</Box>{item.carrierStatus?.replaceAll('_', ' ') || 'Not started'}</div><div><Box variant="awsui-key-label">Tracking</Box>{item.shippingCarrier || '—'}<br />{item.trackingNumber || '—'}{item.trackingUrl && <><br /><Link external href={item.trackingUrl}>Carrier tracking</Link></>}</div><div><Box variant="awsui-key-label">Reimbursement</Box>{item.reimbursementStatus?.replaceAll('_', ' ') || 'Not requested'}{item.reimbursementAmount && <><br />{item.reimbursementAmount} {item.reimbursementCurrency} via {item.paymentMethod}<br />Destination: {item.paymentDestination}</>}</div></ColumnLayout>
      {item.prepaidRates?.length > 0 && item.labelPurchaseState === 'review_required' && <SpaceBetween size="m">{item.prepaidParcel && <ColumnLayout columns={2}><div><Box variant="awsui-key-label">Contributor origin</Box>{item.prepaidOriginSummary || 'Private origin supplied to carrier'}</div><div><Box variant="awsui-key-label">Measured parcel</Box>{item.prepaidParcel.length} × {item.prepaidParcel.width} × {item.prepaidParcel.height} in · {item.prepaidParcel.weight} oz<br />Contributor attested accurate</div></ColumnLayout>}<StatusIndicator type="warning">No carrier rate fit the request’s automatic spending cap. Nothing was purchased.</StatusIndicator><ColumnLayout columns={2}><FormField label="Exceptional prepaid carrier rate"><Select selectedOption={item.prepaidRates.map(rate => ({ value: rate.id, label: `${rate.carrier} ${rate.service} — ${rate.amount} ${rate.currency}${rate.deliveryDays ? ` · ${rate.deliveryDays} days` : ''}` })).find(option => option.value === rateId)} options={item.prepaidRates.map(rate => ({ value: rate.id, label: `${rate.carrier} ${rate.service} — ${rate.amount} ${rate.currency}${rate.deliveryDays ? ` · ${rate.deliveryDays} days` : ''}` }))} onChange={event => setRateId(event.detail.selectedOption.value)} /></FormField><Box padding={{ top: 'l' }}><Button variant="primary" onClick={buyLabel}>Override cap and buy label</Button></Box></ColumnLayout>{chosenRate?.carrier === 'USPS' && <StatusIndicator type="warning">USPS postage is generally charged when the label is created; unused labels require a timely refund request.</StatusIndicator>}</SpaceBetween>}
      {item.reimbursementStatus === 'submitted' && <SpaceBetween direction="horizontal" size="xs"><Button onClick={receipt}>Open receipt (5-minute link)</Button><Button disabled={item.status !== 'received'} variant="primary" onClick={reimbursed}>Mark reimbursement paid</Button>{item.status !== 'received' && <StatusIndicator type="warning">Payment locked until status is Physically received</StatusIndicator>}</SpaceBetween>}
    </SpaceBetween></Container>}
    <ColumnLayout columns={2}><FormField label="Workflow status"><Select selectedOption={statusOptions.find(option => option.value === item.status)} options={statusOptions} onChange={event => update('status', event.detail.selectedOption.value)} /></FormField><FormField label="Private maintainer notes"><Textarea value={item.adminNotes || ''} onChange={event => update('adminNotes', event.detail.value)} rows={5} /></FormField></ColumnLayout>
    <SpaceBetween direction="horizontal" size="xs"><Button onClick={() => save(item)}>Save notes/status</Button><Button onClick={() => openDecision('request_info')}>Request info</Button><Button onClick={() => openDecision('hold')}>Hold</Button><Button onClick={() => openDecision('decline')}>Decline</Button><Button variant="primary" onClick={() => openDecision('approve')}>Approve</Button></SpaceBetween>
    <div><Header variant="h3">History</Header>{(data.history || []).map(event => <div className="history" key={event.sk}><Badge color={event.action === 'approve' ? 'green' : 'grey'}>{event.action}</Badge><span>{new Date(event.createdAt).toLocaleString()} · {event.actor}</span>{event.message && <p>{event.message}</p>}</div>)}</div>
  </SpaceBetween></Container>;
}

function Catalog({ api, notify }) {
  const [items, setItems] = useState([]); const [loading, setLoading] = useState(true); const [selected, setSelected] = useState([]); const [editing, setEditing] = useState(null);
  const load = () => { setLoading(true); api('/api/admin/catalog').then(data => setItems(data.models || [])).catch(error => notify('error', displayError(error))).finally(() => setLoading(false)); };
  useEffect(load, []);
  const { items: shown, collectionProps, filterProps, paginationProps } = useCollection(items, { filtering: { empty: 'No models.', noMatch: 'No matching models.' }, pagination: { pageSize: 25 }, sorting: { defaultState: { sortingColumn: { sortingField: 'position' }, isDescending: false } } });
  const save = async () => { try { await api(editing.id && items.some(item => item.id === editing.id) ? `/api/admin/catalog/${editing.id}` : '/api/admin/catalog', { method: editing.id && items.some(item => item.id === editing.id) ? 'PUT' : 'POST', body: JSON.stringify(editing) }); setEditing(null); notify('success', 'Compatibility entry saved.'); load(); } catch (error) { notify('error', displayError(error)); } };
  const remove = async () => { if (!selected[0] || !confirm(`Delete ${selected[0].productName}?`)) return; try { await api(`/api/admin/catalog/${selected[0].id}`, { method: 'DELETE' }); setSelected([]); notify('success', 'Compatibility entry deleted.'); load(); } catch (error) { notify('error', displayError(error)); } };
  return <><Table {...collectionProps} items={shown} loading={loading} selectionType="single" selectedItems={selected} onSelectionChange={event => setSelected(event.detail.selectedItems)} filter={<TextFilter {...filterProps} filteringPlaceholder="Search products and model numbers" />} pagination={<Pagination {...paginationProps} />}
    header={<Header counter={`(${items.length})`} actions={<SpaceBetween direction="horizontal" size="xs"><Button disabled={!selected.length} onClick={remove}>Delete</Button><Button disabled={!selected.length} onClick={() => setEditing({ ...selected[0] })}>Edit</Button><Button variant="primary" onClick={() => setEditing({ ...blankModel })}>Add model</Button></SpaceBetween>}>Compatibility catalog</Header>}
    columnDefinitions={[
      { id: 'position', header: '#', cell: item => item.position, sortingField: 'position' }, { id: 'category', header: 'Category', cell: item => item.category, sortingField: 'category' },
      { id: 'product', header: 'Product', cell: item => <><strong>{item.productName}</strong><br /><code>{item.modelNumber}</code></>, sortingField: 'productName' },
      { id: 'status', header: 'Evidence status', cell: item => catalogStatuses.find(option => option.value === item.status)?.label || item.status, sortingField: 'status' },
      { id: 'wanted', header: 'Hardware', cell: item => item.hardwareWanted ? <Badge color="red">Wanted</Badge> : <Badge color="green">On hand</Badge> },
    ]} />
    <ModelModal model={editing} setModel={setEditing} save={save} close={() => setEditing(null)} />
  </>;
}

function ModelModal({ model, setModel, save, close }) {
  if (!model) return null;
  const set = (key, value) => setModel(current => ({ ...current, [key]: value }));
  return <Modal visible onDismiss={close} size="large" header={model.productName || 'New compatibility entry'} footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs"><Button onClick={close}>Cancel</Button><Button variant="primary" onClick={save}>Save entry</Button></SpaceBetween></Box>}><SpaceBetween size="m"><ColumnLayout columns={2}>
    <FormField label="Stable ID"><Input value={model.id} onChange={event => set('id', event.detail.value)} placeholder="camera-dbc350" /></FormField><FormField label="Display position"><Input type="number" value={String(model.position)} onChange={event => set('position', Number(event.detail.value))} /></FormField>
    <FormField label="Category"><Input value={model.category} onChange={event => set('category', event.detail.value)} /></FormField><FormField label="Product name"><Input value={model.productName} onChange={event => set('productName', event.detail.value)} /></FormField>
    <FormField label="Exact model / part number"><Input value={model.modelNumber} onChange={event => set('modelNumber', event.detail.value)} /></FormField><FormField label="Generation"><Input value={model.generation || ''} onChange={event => set('generation', event.detail.value)} /></FormField>
    <FormField label="Evidence status"><Select selectedOption={catalogStatuses.find(option => option.value === model.status)} options={catalogStatuses} onChange={event => set('status', event.detail.selectedOption.value)} /></FormField><FormField label="Physical hardware needed"><Toggle checked={model.hardwareWanted} onChange={event => set('hardwareWanted', event.detail.checked)}>Show offer button publicly</Toggle></FormField>
  </ColumnLayout><FormField label="What is proven"><Textarea value={model.tested} onChange={event => set('tested', event.detail.value)} rows={4} /></FormField><FormField label="What still needs proof"><Textarea value={model.needed} onChange={event => set('needed', event.detail.value)} rows={4} /></FormField><ColumnLayout columns={2}><FormField label="Official evidence URL"><Input value={model.sourceUrl || ''} onChange={event => set('sourceUrl', event.detail.value)} /></FormField><FormField label="Evidence reviewed date"><Input value={model.evidenceReviewedAt || ''} onChange={event => set('evidenceReviewedAt', event.detail.value)} placeholder="2026-07-16" /></FormField></ColumnLayout></SpaceBetween></Modal>;
}

function Settings({ api, notify }) {
  const [value, setValue] = useState(null); const [saving, setSaving] = useState(false);
  useEffect(() => { api('/api/admin/settings').then(setValue).catch(error => notify('error', displayError(error))); }, []);
  const setPublic = (key, next) => setValue(current => ({ ...current, public: { ...current.public, [key]: next } }));
  const setTemplate = (key, next) => setValue(current => ({ ...current, templates: { ...current.templates, [key]: next } }));
  const setAddress = (key, next) => setValue(current => ({ ...current, shippingAddress: { ...(current.shippingAddress || {}), [key]: next } }));
  const setCarrier = (carrier, key, next) => setValue(current => ({ ...current, carrierCredentials: { ...(current.carrierCredentials || {}), [carrier]: { ...(current.carrierCredentials?.[carrier] || {}), [key]: next } } }));
  const save = async () => { setSaving(true); try { const data = await api('/api/admin/settings', { method: 'PUT', body: JSON.stringify(value) }); setValue(data); notify('success', 'Public wording, email templates, and private shipping instructions saved.'); } catch (error) { notify('error', displayError(error)); } finally { setSaving(false); } };
  if (!value) return <StatusIndicator type="loading">Loading settings</StatusIndicator>;
  return <SpaceBetween size="l"><Container header={<Header variant="h2">Public intake</Header>}><SpaceBetween size="m"><Toggle checked={value.public.acceptingOffers} onChange={event => setPublic('acceptingOffers', event.detail.checked)}>Accept new hardware offers</Toggle><FormField label="Heading"><Input value={value.public.title} onChange={event => setPublic('title', event.detail.value)} /></FormField><FormField label="Introduction"><Textarea value={value.public.intro} onChange={event => setPublic('intro', event.detail.value)} rows={4} /></FormField><FormField label="Privacy warning"><Textarea value={value.public.privacy} onChange={event => setPublic('privacy', event.detail.value)} rows={4} /></FormField></SpaceBetween></Container>
    <Container header={<Header variant="h2">Private shipping instructions</Header>}><SpaceBetween size="m"><p>These instructions are stored in Secrets Manager, loaded only after you approve a request, and never copied into the contributor record.</p><FormField label="Approval-only shipping details"><Textarea value={value.shippingInstructions || ''} onChange={event => setValue(current => ({ ...current, shippingInstructions: event.detail.value }))} rows={8} /></FormField></SpaceBetween></Container>
    <Container header={<Header variant="h2">Prepaid labels and direct carrier tracking</Header>}><SpaceBetween size="l"><div><StatusIndicator type={value.easyPostConfigured ? 'success' : 'pending'}>{value.easyPostConfigured ? 'Prepaid label purchasing connected' : 'Prepaid label purchasing not connected'}</StatusIndicator><p>EasyPost is used only behind the scenes to compare rates and purchase a carrier label. VivintOne owns the portal, tracking lifecycle, and emails.</p></div><FormField label="New EasyPost production API key" description="Write-only. Leave blank to keep the existing key."><Input type="password" value={value.easyPostApiKey || ''} onChange={event => setValue(current => ({ ...current, easyPostApiKey: event.detail.value }))} /></FormField><ColumnLayout columns={2}><FormField label="Global automatic-label cap (USD)" description="Per-request approval can be lower, never higher than this cap."><Input type="number" value={String(value.maxLabelAmount || '50.00')} onChange={event => setValue(current => ({ ...current, maxLabelAmount: event.detail.value }))} /></FormField><FormField label="Auto-refund unused labels after days" description="0 disables. Maximum 30 days."><Input type="number" value={String(value.autoRefundUnusedLabelsDays ?? 7)} onChange={event => setValue(current => ({ ...current, autoRefundUnusedLabelsDays: Number(event.detail.value) }))} /></FormField></ColumnLayout><Header variant="h3">Direct USPS / UPS / FedEx tracking</Header><p>These write-only OAuth credentials let the VivintOne poller query official carrier APIs. They are stored in Secrets Manager; carrier status updates are normalized and emailed by VivintOne.</p><ColumnLayout columns={3}>{['usps','ups','fedex'].map(carrier => <Container key={carrier} header={<Header variant="h3">{carrier.toUpperCase()}</Header>}><SpaceBetween size="s"><StatusIndicator type={value.directCarrierConfigured?.[carrier] ? 'success' : 'pending'}>{value.directCarrierConfigured?.[carrier] ? 'Configured' : 'Not configured'}</StatusIndicator><FormField label="Client ID"><Input type="password" value={value.carrierCredentials?.[carrier]?.clientId || ''} onChange={event => setCarrier(carrier, 'clientId', event.detail.value)} /></FormField><FormField label="Client secret"><Input type="password" value={value.carrierCredentials?.[carrier]?.clientSecret || ''} onChange={event => setCarrier(carrier, 'clientSecret', event.detail.value)} /></FormField></SpaceBetween></Container>)}</ColumnLayout><Header variant="h3">Hardware Lab destination address</Header><ColumnLayout columns={2}>{[
      ['name','Recipient name'],['company','Company / lab name'],['street1','Street address'],['street2','Apartment / suite'],['city','City'],['state','State'],['zip','ZIP code'],['country','Country code'],['phone','Carrier phone'],
    ].map(([key,label]) => <FormField key={key} label={label}><Input value={value.shippingAddress?.[key] || (key === 'country' ? 'US' : '')} onChange={event => setAddress(key, event.detail.value)} /></FormField>)}</ColumnLayout></SpaceBetween></Container>
    <Container header={<Header variant="h2">Contributor emails</Header>}><SpaceBetween size="l">{Object.entries(value.templates).map(([key, text]) => <FormField key={key} label={key.replace(/([A-Z])/g, ' $1').replace(/^./, char => char.toUpperCase())}><Textarea value={text} rows={key.endsWith('Subject') ? 2 : 8} onChange={event => setTemplate(key, event.detail.value)} /></FormField>)}</SpaceBetween></Container>
    <Box float="right"><Button variant="primary" loading={saving} onClick={save}>Save all settings</Button></Box>
  </SpaceBetween>;
}

createRoot(document.getElementById('root')).render(<App />);
