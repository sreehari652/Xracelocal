from django.shortcuts import render

def tag_manager_page(request):
    return render(request, 'race/tag_manager.html')