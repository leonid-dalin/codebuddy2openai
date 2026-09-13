# How-to: diagnose a content-filter rejection

The backend applies keyword-based content filtering before inference. Requests with trigger terms are refused before the model sees them, and the symptom is an error or a `finish_reason` of `content-filter` rather than an answer.

## 1. Confirm that is what happened

Start the proxy with a log, reproduce the request, and read it back:

```bash
python3 -m workbuddy2openai.converter --log proxy.log
grep "content-filter" proxy.log
```

Streaming responses carry the marker when the filter fired; non-streaming rejections surface as the upstream error body.

## 2. Find the trigger

The common culprit is not user input: clients such as ZCode prepend a fixed compliance template to the system message ("Refuse requests for DoS attacks, exploit development, credential testing..."). Those are refusal declarations, but the filter matches keywords, so the template trips it. Check the logged request body for security terms in the system message.

## 3. Mask the template terms

```bash
python3 -m workbuddy2openai.converter --mask
```

`--mask` inserts a zero-width space into the known terms, but only in system messages. The model reads the words identically; the backend's substring match no longer fires. See [masking.py](../../src/workbuddy2openai/masking.py) for the term list.

## Limits

- the mask covers client-authored templates, not arbitrary text; a genuinely harmful user request is filtered with or without `--mask`
- if rejections continue with `--mask` on, the backend's term list has moved; check whether the logged request still contains unmasked trigger terms and add them upstream of this proxy if you control the template
