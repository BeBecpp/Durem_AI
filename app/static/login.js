const form = document.getElementById('loginForm');
const errorBox = document.getElementById('errorBox');
const password = document.getElementById('password');

document.getElementById('togglePassword').addEventListener('click', (event) => {
  const visible = password.type === 'text';
  password.type = visible ? 'password' : 'text';
  event.currentTarget.textContent = visible ? 'Харах' : 'Нуух';
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  const button = document.getElementById('loginBtn');
  button.disabled = true;
  const old = button.innerHTML;
  button.textContent = 'Нэвтэрч байна…';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type':'application/json'}, credentials:'same-origin',
      body: JSON.stringify({username: document.getElementById('username').value.trim(), password: password.value})
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || 'Нэвтэрч чадсангүй.');
    sessionStorage.setItem('durem_csrf', body.csrf_token || '');
    location.href = body.user?.is_admin ? '/admin' : '/';
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
    button.innerHTML = old;
  }
});
