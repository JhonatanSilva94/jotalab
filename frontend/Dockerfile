FROM nginx:alpine

# Substitui a config padrão do nginx pela nossa
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Site estático: o que está em site/ vira a raiz servida pelo nginx
COPY site/ /usr/share/nginx/html/

EXPOSE 80
