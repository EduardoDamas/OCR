# Relatório Final — Leitura Automática das Cartelas (Bolão EXTRA)

**Data:** 27/06/2026
**Módulo:** Reconhecimento automático das folhas no formato **BOLÃO EXTRA / FOLHA Nº**

---

## 1. Resumo

O sistema lê automaticamente as marcações (Casa / Empate / Fora) das cartelas
fotografadas ou digitalizadas e devolve os resultados já organizados, sem
digitação manual.

A versão anterior conseguia ler corretamente cerca de **50%** das marcações
nas folhas mais fracas (cópias de carbono apagadas, fotos tortas). A nova versão
eleva esse número para **93% a 97%**, mesmo em folhas que o sistema **nunca tinha
visto antes**.

| Conjunto de folhas | Situação | Precisão |
|---|---|---|
| Folhas 11–20 | Usadas para calibrar o sistema | **97,2%** |
| Folhas 21–30 | **Totalmente novas / nunca vistas** | **93,1%** |

> A medição mais importante é a das folhas 21–30, porque são páginas novas — é o
> resultado mais próximo do uso real no dia a dia.

---

## 2. Resultado folha a folha (páginas novas 21–30)

| Folha | Precisão |
|------:|:--------:|
| 21 | 71,4% |
| 22 | 74,5% |
| 23 | 100% |
| 24 | 98,4% |
| 25 | 99,0% |
| 26 | 99,5% |
| 27 | 91,7% |
| 28 | 100% |
| 29 | 96,9% |
| 30 | 100% |

**8 das 10 folhas ficaram entre 91,7% e 100%.** As duas folhas mais fracas (21 e
22) eram casos difíceis: a folha 22 foi fotografada com o papel **dobrado/ondulado**
e a folha 21 tinha várias cartelas preenchidas com um traço muito **fraco e
repetido**. Mesmo nesses casos, o sistema **sinalizou as páginas para conferência**
(veja o item 3) em vez de gravar valores errados em silêncio.

---

## 3. Segurança: o sistema nunca "chuta" sem avisar

Como se trata de dinheiro, o ponto mais importante não é só acertar — é **não
errar em silêncio**. Por isso o sistema marca para **revisão manual** toda vez que:

- uma marcação fica **ambígua** (duas opções com tinta parecida, rasura, marca em
  cima da linha); ou
- a **página inteira** ficou difícil de alinhar (foto muito torta ou ondulada).

Resultado: a taxa de **erro silencioso** (marcação errada **e** não sinalizada
para revisão) nas folhas novas é de apenas **0,68%** — ou seja, menos de 1 em
cada 140 marcações. O restante dos erros já vem destacado na tela para o operador
conferir em segundos.

Na prática: o operador revisa rapidamente apenas as marcações destacadas, e o
risco de um palpite errado entrar no sistema sem ninguém perceber é mínimo.

---

## 4. Características técnicas

- **Gratuito e offline:** funciona apenas com bibliotecas livres (OpenCV + NumPy).
  **Não** depende de nenhum serviço pago, nem de internet, nem de mensalidade.
- **Detecção automática do formato:** o sistema reconhece sozinho quando a página
  é uma folha "BOLÃO EXTRA" e aplica o leitor correto.
- **Robusto a fotos reais:** lida com cópias de carbono apagadas, leve inclinação
  e sombras. Continua funcionando onde a leitura por linhas falhava.

---

## 5. Recomendação de uso (para máxima precisão)

A maior parte dos poucos erros vem de **fotos com o papel dobrado/ondulado ou
muito inclinado**. Para chegar perto de 99–100% (como nas folhas 23, 28 e 30):

1. Apoiar a folha em uma **superfície plana**.
2. Fotografar **de cima**, o mais reto possível.
3. Boa iluminação, evitando sombra forte sobre a cartela.

Folhas fora desse padrão continuam sendo lidas — apenas com mais marcações
enviadas para conferência manual.

---

## 6. Validação e garantia de qualidade

Foi criado um **teste automático de precisão** que roda o leitor sobre as 20
folhas reais já conferidas (11–30) e verifica que a precisão se mantém nos níveis
acima. Esse teste protege o sistema contra qualquer alteração futura que possa,
sem querer, piorar a leitura.

- Precisão exigida pelo teste: ≥ 96% (folhas de referência) e ≥ 92% (folhas novas).
- Limite de erro silencioso vigiado automaticamente.

---

## 7. Conclusão

A leitura automática passou de **~50%** para **93% em páginas inteiramente novas**
(e ~97% nas de referência), de forma **gratuita, offline e segura**, com um
mecanismo que **destaca as dúvidas para conferência** em vez de errar em silêncio.
O módulo está integrado ao aplicativo e coberto por teste automático de qualidade.
