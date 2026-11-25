function formatCurrency(value) {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0,
  }).format(value);
}

function renderBids(bids) {
  const list = document.querySelector("#bid-list");
  list.innerHTML = bids.slice(0, 5).map((bid, index) => `
    <div class="bid-row">
      <span class="rank">${String(index + 1).padStart(2, "0")}</span>
      <strong>${formatCurrency(bid.amount)}</strong>
      <span>${bid.bidder}</span>
      <small>#${bid.sequence} · backend</small>
    </div>
  `).join("");
}

function applyState(snapshot) {
  const bids = snapshot.bids ?? [];
  const winner = snapshot.winner;
  if (winner) {
    document.querySelector("#current-bid").textContent = formatCurrency(winner.amount);
  }
  if (snapshot.reserve) {
    document.querySelector("#reserve-value").textContent = formatCurrency(snapshot.reserve);
    const progress = winner ? Math.min(100, (winner.amount / snapshot.reserve) * 85) : 86;
    document.querySelector("#reserve-progress").style.width = `${progress}%`;
  }
  renderBids(bids);

  if (snapshot.risk) {
    document.querySelector("#risk-score").textContent = String(snapshot.risk.score).padStart(2, "0");
    document.querySelector("#risk-gauge i").style.marginLeft = `${Math.min(92, snapshot.risk.score)}%`;
    document.querySelector("#ai-log p").textContent =
      snapshot.risk.level === "ALTO"
        ? "Atencao: velocidade repetida detectada · revisao ativada."
        : "Analise concluida · nenhuma anomalia critica.";
  }
}

async function loadState() {
  try {
    const response = await fetch("/api/state");
    applyState(await response.json());
  } catch {
    document.querySelector("#bid-feedback").textContent = "Backend indisponivel";
  }
}

function getSession() {
  try {
    return JSON.parse(localStorage.getItem("auction-session"));
  } catch {
    return null;
  }
}

const loginScreen = document.querySelector("#login-screen");
const registerScreen = document.querySelector("#register-screen");
const loginForm = document.querySelector("#login-form");
const registerForm = document.querySelector("#register-form");
const loginFeedback = document.querySelector("#login-feedback");
const registerFeedback = document.querySelector("#register-feedback");

function showLoginScreen() {
  registerScreen.hidden = true;
  loginScreen.hidden = false;
  loginFeedback.textContent = "";
  document.querySelector("#login-email").focus();
}

function showRegisterScreen() {
  loginScreen.hidden = true;
  registerScreen.hidden = false;
  registerFeedback.textContent = "";
  document.querySelector("#register-name").focus();
}

function enterSession(result) {
  localStorage.setItem("auction-session", JSON.stringify(result));
  loginScreen.hidden = true;
  registerScreen.hidden = true;
  document.querySelector(".bid-form label").textContent = `Seu lance · ${result.user.name}`;
  document.querySelector("#bid-feedback").textContent = `Conectado como ${result.user.email}`;
  loadState();
}

const existingSession = getSession();
if (existingSession?.authenticated && existingSession?.token) {
  loginScreen.hidden = true;
  registerScreen.hidden = true;
  document.querySelector(".bid-form label").textContent = `Seu lance · ${existingSession.user.name}`;
  document.querySelector("#bid-feedback").textContent = `Conectado como ${existingSession.user.email}`;
  loadState();
} else {
  showLoginScreen();
}

document.querySelector("#open-register").addEventListener("click", showRegisterScreen);
document.querySelector("#back-to-login").addEventListener("click", showLoginScreen);

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginFeedback.textContent = "Validando...";
  loginFeedback.style.color = "var(--muted)";

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: document.querySelector("#login-email").value,
        password: document.querySelector("#login-password").value,
      }),
    });
    const result = await response.json();
    if (!response.ok || !result.authenticated) {
      loginFeedback.textContent = result.error || "Nao foi possivel entrar.";
      loginFeedback.style.color = "var(--danger)";
      return;
    }
    enterSession(result);
  } catch {
    loginFeedback.textContent = "Falha ao conectar ao servidor.";
    loginFeedback.style.color = "var(--danger)";
  }
});

const cpfInput = document.querySelector("#register-cpf");
cpfInput.addEventListener("input", () => {
  const digits = cpfInput.value.replace(/\D/g, "").slice(0, 11);
  cpfInput.value = digits
    .replace(/(\d{3})(\d)/, "$1.$2")
    .replace(/(\d{3})(\d)/, "$1.$2")
    .replace(/(\d{3})(\d{1,2})$/, "$1-$2");
});


registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  registerFeedback.textContent = "Criando conta...";
  registerFeedback.style.color = "var(--muted)";

  try {
    const response = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.querySelector("#register-name").value,
        cpf: document.querySelector("#register-cpf").value,
        email: document.querySelector("#register-email").value,
        password: document.querySelector("#register-password").value,
      }),
    });
    const result = await response.json();
    if (!response.ok || !result.authenticated) {
      registerFeedback.textContent = result.error || "Nao foi possivel criar a conta.";
      registerFeedback.style.color = "var(--danger)";
      return;
    }
    enterSession(result);
  } catch {
    registerFeedback.textContent = "Falha ao conectar ao servidor.";
    registerFeedback.style.color = "var(--danger)";
  }
});

document.querySelector("#logout-button").addEventListener("click", () => {
  localStorage.removeItem("auction-session");
  loginForm.reset();
  registerForm.reset();
  showLoginScreen();
});

document.querySelector("#bid-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#bid-value");
  const amount = Number(input.value);
  const feedback = document.querySelector("#bid-feedback");
  const bidId = crypto.randomUUID();
  const session = getSession();
  const bidder = session?.user?.bidder || "#YOU-01";

  if (!Number.isFinite(amount)) {
    feedback.textContent = "Lance rejeitado · valor invalido";
    feedback.style.color = "var(--danger)";
    return;
  }

  try {
    const response = await fetch("/api/bids", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": bidId,
      },
      body: JSON.stringify({ bidder, amount, bidId }),
    });
    const result = await response.json();
    applyState(result.state);
    if (response.ok && result.accepted) {
      input.value = amount + 500;
      feedback.textContent = "Lance aceito · publicado no backend com sequencia confirmada";
      feedback.style.color = "var(--accent)";
    } else {
      feedback.textContent = result.error || "Lance rejeitado";
      feedback.style.color = "var(--danger)";
    }
  } catch {
    feedback.textContent = "Falha ao conectar ao backend";
    feedback.style.color = "var(--danger)";
  }
});

const codeSamples = {
  strategy: `class HardCloseStrategy {
  resolve(current, deadline, now) {
    return now >= deadline ? current.winner : null
  }
}`,
  observer: `engine.subject.subscribe({
  update(event) {
    if (event.type === "BID_ACCEPTED") {
      notifyUi(event.bid)
      fraudAgent.analyze(event.bid)
    }
  }
})`,
};

document.querySelectorAll(".code-tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".code-tabs button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.querySelector("#code-preview code").textContent = codeSamples[button.dataset.code];
  });
});

function updateClock() {
  document.querySelector("#clock").textContent = new Date().toISOString().slice(11, 19);
}

let remaining = 8077;
function updateCountdown() {
  remaining = Math.max(0, remaining - 1);
  const hours = String(Math.floor(remaining / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((remaining % 3600) / 60)).padStart(2, "0");
  const seconds = String(remaining % 60).padStart(2, "0");
  document.querySelector("#countdown").textContent = `${hours}:${minutes}:${seconds}`;
}

loadState();
updateClock();
updateCountdown();
setInterval(updateClock, 1000);
setInterval(updateCountdown, 1000);

const events = new EventSource("/api/events");
events.onmessage = (message) => {
  const event = JSON.parse(message.data);
  if (event.state) applyState(event.state);
};
