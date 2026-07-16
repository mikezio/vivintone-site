export async function portalRequest(session, path, options = {}) {
  // The portal authorizer uses verified email/phone identity claims from the ID token.
  const headers = { accept: 'application/json', authorization: `Bearer ${session.idToken}`, ...options.headers };
  if (options.body && !(options.body instanceof FormData)) headers['content-type'] = 'application/json';
  const response = await fetch(`/api/portal${path}`, { cache: 'no-store', ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 || response.status === 403) {
    const error = new Error('Your secure session has ended. Sign in again to continue.');
    error.sessionExpired = true;
    throw error;
  }
  if (!response.ok) throw new Error(data.message || friendlyError(data.error));
  return data;
}

function friendlyError(code = '') {
  const messages = {
    invalid_tracking_number: 'Check the tracking number and try again.',
    prepaid_label_not_authorized: 'A prepaid label is not available for this request.',
    prepaid_label_already_exists: 'A prepaid label has already been created for this request.',
    shipping_details_attestation_required: 'Confirm that the packed measurements and return address are accurate.',
    no_eligible_rate_within_cap: 'No eligible label was available. The Hardware Lab will review the shipment without purchasing postage.',
    receipt_upload_failed: 'The receipt could not be uploaded. Try again with a JPEG, PNG, or PDF under 10 MB.',
  };
  return messages[code] || 'Something went wrong. Your request was not changed; please try again.';
}

export function requestList(data) { return Array.isArray(data) ? data : (data.requests || data.items || []); }
