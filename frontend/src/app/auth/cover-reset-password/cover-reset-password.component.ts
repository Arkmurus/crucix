import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-cover-reset-password',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './cover-reset-password.component.html',
  styleUrl: './cover-reset-password.component.scss'
})
export class CoverResetPasswordComponent implements OnInit {
  constructor(private router: Router) {}
  ngOnInit(): void { this.router.navigate(['/auth/reset-password']); }
}
