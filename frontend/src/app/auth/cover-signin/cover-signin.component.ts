import { Component, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-cover-signin',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './cover-signin.component.html',
  styleUrl: './cover-signin.component.scss'
})
export class CoverSigninComponent implements OnInit {
  constructor(private router: Router) {}
  ngOnInit(): void { this.router.navigate(['/auth/sign-in']); }
}
