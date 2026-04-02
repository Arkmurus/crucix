import { Component, OnInit } from '@angular/core';
import { FormBuilder, FormGroup, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-sign-in',
  templateUrl: './sign-in.component.html',
  styleUrls: ['./sign-in.component.scss']
})
export class SignInComponent implements OnInit {
  loginForm!: FormGroup;
  twoFaForm!: FormGroup;

  step: 'credentials' | '2fa' = 'credentials';
  preToken = '';

  loading = false;
  errorMsg = '';
  hide = true;

  constructor(
    private fb: FormBuilder,
    private authService: AuthService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.loginForm = this.fb.group({
      email:    ['', [Validators.required, Validators.email]],
      password: ['', Validators.required]
    });
    this.twoFaForm = this.fb.group({
      code: ['', [Validators.required, Validators.pattern(/^\d{6}$/)]]
    });
  }

  onSubmit(): void {
    if (this.loginForm.invalid) { this.loginForm.markAllAsTouched(); return; }
    this.loading = true;
    this.errorMsg = '';
    const { email, password } = this.loginForm.value;
    this.authService.login(email, password).subscribe({
      next: (res: any) => {
        this.loading = false;
        if (res.requires2FA) {
          this.preToken = res.preToken;
          this.step = '2fa';
        } else {
          this.router.navigate(['/dashboard/brief']);
        }
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.error || err?.error?.message || 'Sign in failed.';
      }
    });
  }

  onVerify2FA(): void {
    if (this.twoFaForm.invalid) { this.twoFaForm.markAllAsTouched(); return; }
    this.loading = true;
    this.errorMsg = '';
    const { code } = this.twoFaForm.value;
    this.authService.twoFaAuthenticate(this.preToken, code).subscribe({
      next: () => {
        this.loading = false;
        this.router.navigate(['/dashboard/brief']);
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.error || 'Invalid code. Try again.';
      }
    });
  }

  backToCredentials(): void {
    this.step = 'credentials';
    this.preToken = '';
    this.errorMsg = '';
    this.twoFaForm.reset();
  }
}
