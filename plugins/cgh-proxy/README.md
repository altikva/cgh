# cgh-proxy (experimental)

A local proxy between Claude Code and a LiteLLM gateway, for
[cgh](https://github.com/altikva/cgh), the context graph hub. It does
three things:

- it sends every request to the gateway over TLS, with a client
  certificate when you give one (mTLS);
- it keeps a copy of the last exchanges and a journal, so you can see
  what Claude sends and what each call costs in tokens;
- it counts the PII and secrets found in outgoing requests. It does not
  mask or block anything yet.

```bash
uv tool install --force -U cgh --with cgh-pii --with ./plugins/cgh-proxy
cgh proxy serve --upstream https://litellm.example.com \
    --cert client.crt --key client.key --ca gateway-ca.crt

# in the shell that runs Claude Code
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_AUTH_TOKEN=<your LiteLLM key>
claude
```

```bash
cgh proxy list              # last exchanges: size, tokens, PII in the new message
cgh proxy show <id>         # where the bytes of one request go
cgh proxy show <id> --raw   # the captured request and answer
```

## What leaves the machine, and how

Claude Code talks plain HTTP to the proxy, on 127.0.0.1 only. The
proxy talks HTTPS to the gateway. It refuses a remote gateway over plain
`http`, since the traffic would cross the network in clear. A gateway on
the same machine may use `http`.

The request goes out unchanged, credentials included: the proxy is not
in charge of authentication, LiteLLM is.

## mTLS on the gateway side

LiteLLM does not check client certificates itself. Put a TLS terminator in
front of it that does, for example Caddy:

```
litellm.example.com {
    tls /etc/caddy/gateway.crt /etc/caddy/gateway.key {
        client_auth {
            mode require_and_verify
            trust_pool file /etc/caddy/clients-ca.crt
        }
    }
    reverse_proxy 127.0.0.1:4000
}
```

A small private CA is enough for a test:

```bash
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 365 \
    -keyout clients-ca.key -out clients-ca.crt -subj "/CN=cgh proxy clients"
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout client.key -out client.csr -subj "/CN=$(whoami)@$(hostname)"
openssl x509 -req -in client.csr -CA clients-ca.crt -CAkey clients-ca.key \
    -CAcreateserial -days 90 -out client.crt
```

Without `--cert`, the proxy still uses TLS, with server authentication
only.

## What is stored, and where

Everything lives in `~/.cache/cgh/proxy` (`--dir` to change it), in a
directory only you can read (0700, files 0600).

- `cap-<id>.json`: one full exchange, **raw bodies included**: your
  prompts, the code Claude read, the answers. Only the last `--keep`
  (default 20) are kept; `--keep 0` keeps none.
- `journal.jsonl`: one line per exchange, without any body: sizes,
  model, token usage, and PII counts. It is never pruned.

Headers are stored through an allow-list (content type, API version,
request ids). `Authorization`, `x-api-key` and cookies never reach the
disk.

## PII observation

Each `/v1/messages` request is scanned with the `cgh-pii` scanner
(emails, phones, IBANs, cards, AWS keys, private keys, password
assignments), twice:

- the whole request, which repeats the conversation at every turn;
- its last message alone, which is what this turn adds. `cgh proxy list`
  shows this one.

Only the category and the count are recorded, never the value. Without
`cgh-pii` installed, the journal says `not scanned`. `--no-scan` turns
the scan off.

These are regex patterns: they flag test fixtures and author lines, and
see no names. The counts tell you where to look, not what leaked.

## Settings

Flags win over `[plugin.proxy]` in `.codegraph/config.toml`:

```toml
[plugin.proxy]
upstream = "https://litellm.example.com"
port = 8787
keep = 20
client_cert = "~/.config/cgh/proxy/client.crt"
client_key = "~/.config/cgh/proxy/client.key"
ca_cert = "~/.config/cgh/proxy/gateway-ca.crt"
```

## Not done yet

- Masking PII before it leaves, then blocking secrets: the cgh SDK
  already has `redact_text` and `egress_decision` for it.
- Encrypting the message bodies on top of TLS. This needs a cgh component
  next to LiteLLM to decrypt them.
- Encrypting the captures at rest.
