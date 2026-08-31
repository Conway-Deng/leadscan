"use strict";

(function () {
  const auditForm = document.getElementById("audit-form");
  const websiteInput = document.getElementById("website-url");
  const contactNameInput = document.getElementById("contact-name");
  const contactEmailInput = document.getElementById("contact-email");
  const auditSubmit = document.getElementById("audit-submit");
  const auditStatus = document.getElementById("audit-status");
  const auditResult = document.getElementById("audit-result");
  const resultScore = document.getElementById("result-score");
  const resultTier = document.getElementById("result-tier");
  const resultHook = document.getElementById("result-hook");
  const reportPreview = document.getElementById("report-preview");
  const auditReset = document.getElementById("audit-reset");

  const ERROR_MESSAGES = {
    invalid_request: "Please check the website and contact details and try again.",
    invalid_url: "That website address cannot be reviewed. Check the address and try again.",
    rate_limited: "Too many review requests have been made. Please try again shortly.",
    busy: "The review service is busy right now. Please try again shortly.",
    audit_timeout: "The website took too long to review. Please try again later.",
    audit_failed: "The website could not be reviewed right now. Please try again later.",
    lead_capture_failed: "We could not save your contact details. Please try again later.",
    configuration: "The review service is temporarily unavailable. Please try again later.",
    generic: "The review service returned an unexpected response. Please try again.",
    network: "Could not reach the review service. Check your connection and try again."
  };

  function resolveAuditEndpoint() {
    const meta = document.querySelector('meta[name="leadscan-api-origin"]');
    if (!meta) {
      return null;
    }

    const raw = meta.getAttribute("content") || "";
    const trimmed = raw.trim();
    if (!trimmed) {
      return "/api/audit";
    }

    if (trimmed.includes("*") || trimmed.includes(",") || trimmed.includes("\\")) {
      return null;
    }

    try {
      const parsed = new URL(trimmed);
      if (parsed.protocol !== "https:") {
        return null;
      }
      if (!parsed.hostname) {
        return null;
      }
      if (parsed.username || parsed.password) {
        return null;
      }
      if (parsed.search || parsed.hash) {
        return null;
      }
      if (parsed.pathname !== "/" && parsed.pathname !== "") {
        return null;
      }
      if (!parsed.origin || parsed.origin === "null") {
        return null;
      }
      return parsed.origin + "/api/audit";
    } catch (e) {
      return null;
    }
  }

  function setStatus(message) {
    auditStatus.textContent = message;
  }

  function clearResults() {
    resultScore.textContent = "";
    resultTier.textContent = "";
    resultHook.textContent = "";
    reportPreview.srcdoc = "";
    auditResult.hidden = true;
  }

  function setLoading(isLoading) {
    websiteInput.disabled = isLoading;
    contactNameInput.disabled = isLoading;
    contactEmailInput.disabled = isLoading;
    auditSubmit.disabled = isLoading;
    if (isLoading) {
      auditSubmit.textContent = "Reviewing…";
      setStatus("Reviewing the website. This can take a little while.");
      clearResults();
    } else {
      auditSubmit.textContent = "Review website";
    }
  }

  function handleReset() {
    clearResults();
    setStatus("");
    auditForm.hidden = false;
    websiteInput.disabled = false;
    contactNameInput.disabled = false;
    contactEmailInput.disabled = false;
    auditSubmit.disabled = false;
    auditSubmit.textContent = "Review website";
    websiteInput.value = "";
    contactNameInput.value = "";
    contactEmailInput.value = "";
    websiteInput.focus();
  }

  async function handleAuditSubmit(event) {
    event.preventDefault();

    const submittedUrl = websiteInput.value.trim();
    const contactName = contactNameInput.value.trim();
    const contactEmail = contactEmailInput.value.trim();

    if (!submittedUrl || !contactEmail) {
      setStatus(ERROR_MESSAGES.invalid_request);
      return;
    }

    const auditEndpoint = resolveAuditEndpoint();
    if (!auditEndpoint) {
      setStatus(ERROR_MESSAGES.configuration);
      return;
    }

    setLoading(true);
    let isCompletedSuccess = false;

    try {
      const response = await fetch(auditEndpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          url: submittedUrl,
          contact_name: contactName,
          email: contactEmail
        })
      });

      let data = null;
      try {
        data = await response.json();
      } catch (parseError) {
        setStatus(ERROR_MESSAGES.generic);
        return;
      }

      if (
        response.ok &&
        data &&
        data.ok === true &&
        data.code === "ok" &&
        data.result &&
        typeof data.result.report_html === "string"
      ) {
        isCompletedSuccess = true;
        auditForm.hidden = true;
        setStatus("Review complete.");

        resultScore.textContent = data.result.score != null ? String(data.result.score) : "—";
        resultTier.textContent = data.result.tier ? data.result.tier : "Not ranked";
        resultHook.textContent = data.result.hook ? data.result.hook : "No opening line generated.";

        reportPreview.srcdoc = data.result.report_html;
        auditResult.hidden = false;
      } else {
        const code = data && typeof data.code === "string" ? data.code : "";
        let errorMsg = ERROR_MESSAGES[code] || ERROR_MESSAGES.generic;

        if (code === "rate_limited") {
          const retryAfterHeader = response.headers.get("Retry-After");
          if (retryAfterHeader) {
            const retrySeconds = parseInt(retryAfterHeader.trim(), 10);
            if (!Number.isNaN(retrySeconds) && retrySeconds > 0) {
              errorMsg = errorMsg + " You can try again in about " + retrySeconds + " seconds.";
            }
          }
        }

        setStatus(errorMsg);
      }
    } catch (networkError) {
      setStatus(ERROR_MESSAGES.network);
    } finally {
      if (!isCompletedSuccess) {
        setLoading(false);
      }
    }
  }

  auditForm.addEventListener("submit", handleAuditSubmit);
  auditReset.addEventListener("click", handleReset);
})();
