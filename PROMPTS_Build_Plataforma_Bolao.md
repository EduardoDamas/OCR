# Prompts para construção da Plataforma de Bolão

Sequência de prompts para usar num assistente de código (Claude Code, Cursor, etc.).
Cada prompt é um passo. Rode na ordem, valide cada etapa antes de ir pra próxima.

**Stack recomendada** (produtiva, deploy fácil, boa pra Pix/Mercado Pago):
- **Next.js 14 (App Router) + TypeScript + Tailwind CSS** (front + API numa coisa só)
- **PostgreSQL + Prisma** (banco)
- **NextAuth (Auth.js)** com 3 perfis (roles)
- **SDK Mercado Pago** (Pix: QR Code + Copia-e-Cola + webhook)
- **Puppeteer** ou **pdfmake** (geração de PDF)
- Deploy: VPS (Hostinger/Contabo) ou Vercel + banco gerenciado

---

## Regras de negócio fixas (cole junto com qualquer prompt quando relevante)

```
REGRAS DO BOLÃO:
- Cada bolão/rodada tem exatamente 8 jogos.
- Cada jogo tem 3 opções: CASA / EMPATE / FORA.
- O apostador marca um palpite por jogo. Pode marcar:
  - SECO: 1 opção no jogo
  - DUPLO: 2 opções no mesmo jogo
  - TRIPLO: 3 opções no mesmo jogo
- Preço: R$ 10,00 por bilhete.
- Nº de bilhetes de uma aposta = 2^(qtd de duplos) × 3^(qtd de triplos).
- Valor total da aposta = nº de bilhetes × R$ 10,00.
  Ex: 1 duplo = 2 bilhetes = R$20. 1 triplo = 3 = R$30.
      1 duplo + 1 triplo = 6 = R$60. 3 triplos = 27 = R$270.
- Pontuação: 1 ponto por acerto. Máximo 8 pontos.
  Numa aposta com duplo/triplo, conta como acerto se o resultado oficial
  estiver entre as opções marcadas naquele jogo.
- Jogo anulado/cancelado: sai do cálculo; ranking passa a valer sobre os jogos válidos.
- Premiação é paga MANUALMENTE pelo gerente (fora do sistema, via WhatsApp).
  O sistema só APONTA os ganhadores — não faz pagamento de prêmio nem saque.
```

---

## Prompt 1 — Setup do projeto e modelo de dados

```
Crie um projeto Next.js 14 (App Router) com TypeScript, Tailwind CSS e Prisma
com PostgreSQL. Configure ESLint e estrutura de pastas limpa.

Modele o schema Prisma para uma plataforma de bolão de futebol:

- User: id, nome, whatsapp, email (opcional), senhaHash, role (enum:
  GERENTE | VENDEDOR | APOSTADOR), vendedorId (opcional, aponta o vendedor
  do apostador), createdAt.
- Bolao: id, titulo, status (enum: ABERTO | FECHADO | ENCERRADO),
  valorBilhete (default 1000 = R$10,00 em centavos), fechamentoEm (datetime),
  createdAt.
- Jogo: id, bolaoId, ordem (1..8), timeCasa, timeFora, dataHora,
  resultado (enum opcional: CASA | EMPATE | FORA | ANULADO).
- Bilhete: id, codigo (curto, único, ex "Q4Z61RP"), bolaoId, apostadorId,
  vendedorId (opcional), nomeApostador, whatsapp,
  palpites (JSON: array de 8 itens, cada item = array de 1-3 de
  {CASA|EMPATE|FORA}), qtdSecos, qtdDuplos, qtdTriplos,
  qtdBilhetes, valorCentavos,
  statusPagamento (enum: PENDENTE | PAGO | EXPIRADO),
  pontos (int, calculado), createdAt, pagoEm.
- Pagamento: id, bilheteId, provedor ("mercadopago"), paymentId (do MP),
  qrCode, qrCodeBase64, copiaECola, status, valorCentavos, createdAt.

Gere a migration inicial e um seed com 1 gerente de teste.
Use centavos (int) para todo valor monetário. Documente no README como rodar.
```

---

## Prompt 2 — Autenticação e 3 perfis

```
Implemente autenticação com NextAuth (Auth.js) usando credenciais
(whatsapp/email + senha) e os 3 perfis: GERENTE, VENDEDOR, APOSTADOR.

- Tela de login e tela de cadastro de apostador (nome, whatsapp, senha).
- Cadastro de vendedor só pode ser feito pelo GERENTE.
- Middleware de proteção de rotas por role:
  - /admin/*  -> só GERENTE
  - /vendedor/* -> GERENTE ou VENDEDOR
  - /apostar, /meus-bilhetes -> qualquer logado
- Sessão com role e id no token JWT.
- Hash de senha com bcrypt.
Inclua testes básicos das regras de acesso por role.
```

---

## Prompt 3 — Painel do Gerente: bolões, jogos e vendedores

```
Crie o painel do GERENTE (/admin) com:

1. CRUD de Bolão: criar bolão com título, valor do bilhete e data/hora de
   fechamento; listar bolões com status (ABERTO/FECHADO/ENCERRADO).
2. Ao criar um bolão, cadastrar os 8 jogos (timeCasa, timeFora, data/hora).
   Validar que são exatamente 8.
3. Botão "Fechar apostas" (muda status para FECHADO, bloqueia novos bilhetes).
4. CRUD de Vendedores (criar login de vendedor, listar, ativar/desativar).
5. Dashboard simples: total de bilhetes pagos, total arrecadado (R$),
   nº de apostadores, por bolão.

UI responsiva (mobile-first) com Tailwind. Use Server Actions/Route Handlers.
```

---

## Prompt 4 — Fluxo de aposta (secos/duplos/triplos) + cálculo do bilhete

```
Implemente a tela de aposta (/apostar?bolao=ID), espelhando o fluxo do site de
referência (bolaodaalegria.top):

- Mostrar os 8 jogos do bolão aberto. Cada jogo com 3 botões: Casa / Empate / Fora.
- O apostador pode selecionar 1, 2 ou 3 opções por jogo (seco/duplo/triplo),
  alternando o estado do botão ao clicar.
- Em tempo real, mostrar um resumo: "SECOS: X · DUPLOS: Y · TRIPLOS: Z ·
  Aposta equivale a N bilhete(s) · Valor total: R$ V".
  Onde N = 2^(duplos) × 3^(triplos) e V = N × valorBilhete.
- Campos Nome e WhatsApp (pré-preenchidos se logado).
- Validar: os 8 jogos precisam ter pelo menos 1 palpite (obrigatório).
- Botão "Gerar bilhete": cria o Bilhete com statusPagamento=PENDENTE,
  gera um código curto único, salva palpites e os totais calculados no servidor
  (NUNCA confie no cálculo do cliente — recalcule no backend).

Cole as REGRAS DO BOLÃO acima. Inclua testes da função de cálculo de bilhetes
(secos, 1 duplo, 1 triplo, combinações, 3 triplos=270).
```

---

## Prompt 5 — Integração Mercado Pago (Pix automático)

```
Integre o Mercado Pago para pagamento via Pix (somente recebimento):

- Ao gerar o bilhete, criar um pagamento Pix no Mercado Pago (SDK oficial)
  com o valor total. Salvar paymentId, qrCode, qrCodeBase64 e copiaECola
  no registro Pagamento.
- Tela do bilhete: mostrar QR Code (imagem), código Copia-e-Cola com botão
  "Copiar código", valor, e o código do bilhete (igual ao vídeo de referência:
  "PAGAMENTO VIA PIX").
- Webhook /api/webhooks/mercadopago: ao receber a notificação de pagamento
  aprovado, validar a autenticidade (assinatura/secret), buscar o pagamento,
  e marcar Bilhete.statusPagamento = PAGO e pagoEm = agora.
  -> A partir daí o bilhete entra valendo no bolão AUTOMATICAMENTE.
- Botão "Confirmar pagamento" na tela do bilhete: faz polling/refetch do status
  para atualizar a UI caso o webhook ainda não tenha chegado.
- Idempotência: webhook não pode pagar duas vezes o mesmo bilhete.
- Variáveis de ambiente para o access token do MP (sandbox e produção).

IMPORTANTE: o sistema só RECEBE. Não há saque nem pagamento de premiação pelo
sistema. Documente como configurar as credenciais do Mercado Pago do cliente.
```

---

## Prompt 6 — Bilhete: compartilhar e copiar palpites

```
Na tela do bilhete (/bilhete/CODIGO), adicione (espelhando o vídeo de referência):

- Botão "COMPARTILHAR" via WhatsApp (link wa.me com um resumo do bilhete:
  código, jogos/palpites, valor, status).
- Botão "COPIAR PALPITES PARA NOVO BILHETE": leva o usuário de volta à tela de
  aposta com os mesmos 8 palpites pré-selecionados, pra ele gerar outro bilhete.
- Mostrar status visível: PENDENTE (aguardando pagamento) / PAGO (validado) /
  EXPIRADO.
- Página "/meus-bilhetes": lista os bilhetes do apostador logado com status e link.
```

---

## Prompt 7 — Resultados e Ranking (parcial ao vivo)

```
Implemente lançamento de resultados e ranking:

1. No painel do GERENTE: tela para lançar o resultado oficial de cada um dos
   8 jogos (Casa/Empate/Fora) ou marcar ANULADO. Ao salvar, recalcular os
   pontos de TODOS os bilhetes PAGOS do bolão.
   - Acerto = resultado oficial está entre as opções marcadas no jogo.
   - Jogo ANULADO não conta (reduz o total de jogos válidos).
2. Ranking público (/ranking/BOLAO): lista ordenada por pontos (desc),
   mostrando nome do apostador, vendedor e pontos. Acessível por URL simples,
   mobile, sem precisar instalar app.
3. "Parcial ao vivo": enquanto os jogos rolam, mostrar quantos bilhetes estão
   com pontuação máxima, com 7, com 6, etc. (distribuição de pontos).
4. O ranking mostra SOMENTE os jogos deste site (deste bolão) — sem misturar
   com fontes externas.
Cole as REGRAS DO BOLÃO. Inclua testes de pontuação (seco acerta/erra,
duplo/triplo conta acerto se o resultado estiver entre as opções, jogo anulado).
```

---

## Prompt 8 — Relatórios em PDF

```
Implemente geração de PDF (Puppeteer ou pdfmake):

1. "PDF do dia": todas as apostas PAGAS de um bolão, em formato de cartelas/lista,
   com nome do apostador, vendedor, código do bilhete e os 8 palpites — para
   auditoria/conferência. Botão de download no painel do gerente.
2. "PDF do ranking": o ranking atual do bolão, pronto para imprimir/compartilhar.
Cabeçalho com título do bolão e data. Layout limpo, A4.
```

---

## Prompt 9 — Deploy e produção

```
Prepare a aplicação para produção:

- Dockerfile + docker-compose (app Next.js + PostgreSQL) para deploy em VPS.
- Variáveis de ambiente documentadas (.env.example): DATABASE_URL,
  NEXTAUTH_SECRET, MP_ACCESS_TOKEN, MP_WEBHOOK_SECRET, URL pública.
- Migrations rodando no start. Seed do gerente inicial.
- HTTPS (instruções com Nginx + Certbot, domínio próprio do cliente).
- Checklist de go-live: trocar credenciais MP de sandbox para produção,
  testar webhook com pagamento real de R$1, conferir fechamento de bolão.
- README final: como subir, como o gerente opera o dia a dia (criar bolão,
  fechar, lançar resultados, baixar PDFs).
```

---

## Ordem de execução e validação

1. Prompt 1-2: base + login funcionando (cadastra gerente, loga).
2. Prompt 3-4: gerente cria bolão com 8 jogos, apostador monta aposta e vê o
   valor calculado certo (teste com duplos/triplos).
3. Prompt 5: pagar em SANDBOX e ver o bilhete virar PAGO automático.
4. Prompt 6-7: compartilhar, lançar resultado, ver ranking.
5. Prompt 8-9: PDFs e deploy no domínio do cliente.

> Sempre validar cálculo de bilhetes e pontuação no BACKEND (nunca confiar no
> cliente) — é onde mexe com dinheiro.
```
