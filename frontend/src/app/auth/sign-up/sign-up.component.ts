import { Component, OnInit } from '@angular/core';
import { AbstractControl, FormBuilder, FormGroup, ValidationErrors, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';

function passwordsMatchValidator(group: AbstractControl): ValidationErrors | null {
  const password = group.get('password')?.value;
  const confirm = group.get('confirmPassword')?.value;
  return password === confirm ? null : { passwordsMismatch: true };
}

@Component({
  selector: 'app-sign-up',
  templateUrl: './sign-up.component.html',
  styleUrls: ['./sign-up.component.scss']
})
export class SignUpComponent implements OnInit {
  registerForm!: FormGroup;
  loading = false;
  errorMsg = '';
  successMsg = '';
  hide = true;
  hideConfirm = true;

  constructor(
    private fb: FormBuilder,
    private authService: AuthService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.registerForm = this.fb.group(
      {
        fullName: ['', Validators.required],
        username: ['', [Validators.required, Validators.minLength(3)]],
        email: ['', [Validators.required, Validators.email]],
        password: ['', [Validators.required, Validators.minLength(8)]],
        confirmPassword: ['', Validators.required]
      },
      { validators: passwordsMatchValidator }
    );
  }

  onSubmit(): void {
    if (this.registerForm.invalid) {
      this.registerForm.markAllAsTouched();
      return;
    }
    this.loading = true;
    this.errorMsg = '';
    this.successMsg = '';

    const { fullName, username, email, password } = this.registerForm.value;

    this.authService.register({ fullName, username, email, password }).subscribe({
      next: (res: any) => {
        this.loading = false;
        if (res?.autoActivated) {
          this.successMsg = 'Account created! Redirecting to sign in...';
          setTimeout(() => this.router.navigate(['/auth/sign-in']), 1200);
        } else {
          this.successMsg = 'Registration successful! Please check your email for the verification code.';
          setTimeout(() => {
            this.router.navigate(['/auth/verify-email'], { queryParams: { email } });
          }, 1500);
        }
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.message || 'Registration failed. Please try again.';
      }
    });
  }
}
